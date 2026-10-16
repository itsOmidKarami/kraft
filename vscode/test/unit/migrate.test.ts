import { describe, expect, it, vi } from "vitest";
import { ApiError, type ThreadIn } from "../../src/core/api";
import { dropMessage, migrateAll, migrateItem, toThreadIns, type DropReason, type LegacyDraft, type MigrateDeps } from "../../src/core/migrate";

const draft: LegacyDraft = { comments: [{ file: "a.py", line: 3, body: "x", side: "base" }, { file: "b.py", line: 1, body: "y" }], summary: "s" };
const five: LegacyDraft = { comments: [1, 2, 3, 4, 5].map((n) => ({ file: "f.py", line: n, body: `c${n}` })) };

/** Fake deps that record what was posted and the last thing stored per item. */
function deps(over: Partial<MigrateDeps> = {}) {
  const posted: string[] = [];
  const stored = new Map<string, LegacyDraft | undefined>();
  const d: MigrateDeps = {
    reviewable: async () => true,
    existing: async () => [],
    post: async (_id: string, t: ThreadIn) => void posted.push(t.body),
    save: async (id, v) => void stored.set(id, v),
    ...over,
  };
  return { d, posted, stored };
}

describe("toThreadIns", () => {
  it("makes whole-change threads naming file and line, then the summary", () =>
    expect(toThreadIns(draft)).toEqual([{ body: "a.py:3 (old) — x" }, { body: "b.py:1 — y" }, { body: "s" }]));
  it("adds nothing for a blank summary", () => expect(toThreadIns({ comments: [], summary: "  " })).toEqual([]));
});

describe("migrateItem", () => {
  it("posts each comment once and clears the key", async () => {
    const { d, posted, stored } = deps();
    expect(await migrateItem("K-1", draft, d)).toEqual({ posted: 3, dropped: null, kept: null });
    expect(posted).toEqual(["a.py:3 (old) — x", "b.py:1 — y", "s"]);
    expect(stored.get("K-1")).toBeUndefined();
    expect(stored.has("K-1")).toBe(true);
  });

  it("keeps only what was not posted", async () => {
    let n = 0;
    const first = deps({ post: async () => { if (++n === 3) throw new ApiError(0, "down"); } });
    const r = await migrateItem("K-1", five, first.d);
    expect(r.posted).toBe(2);
    expect(r.kept?.comments.map((c) => c.body)).toEqual(["c3", "c4", "c5"]);
    expect(first.stored.get("K-1")?.comments.map((c) => c.body)).toEqual(["c3", "c4", "c5"]);
    const again = deps();
    await migrateItem("K-1", first.stored.get("K-1")!, again.d);
    expect(again.posted).toEqual(["f.py:3 — c3", "f.py:4 — c4", "f.py:5 — c5"]);
  });

  it("skips a draft already on the server", async () => {
    const { d, posted } = deps({ existing: async () => ["a.py:3 (old) — x"] });
    await migrateItem("K-1", draft, d);
    expect(posted).toEqual(["b.py:1 — y", "s"]);
  });

  it("drops everything when the item has nothing to review", async () => {
    const { d, posted, stored } = deps({ reviewable: async () => false });
    expect(await migrateItem("K-1", draft, d)).toEqual({ posted: 0, dropped: draft, kept: null, why: "unreviewable" });
    expect(posted).toEqual([]);
    expect(stored.has("K-1") && stored.get("K-1")).toBeUndefined();
  });

  it("stops posting once the window turns read-only, keeping the rest", async () => {
    let n = 0;
    const { d, posted, stored } = deps({ writable: () => n < 2, post: async (_id, t) => void (n++, posted.push(t.body)) });
    const r = await migrateItem("K-1", five, d);
    expect(posted).toHaveLength(2);
    expect(r.kept?.comments.map((c) => c.body)).toEqual(["c3", "c4", "c5"]);
    expect(stored.get("K-1")?.comments.map((c) => c.body)).toEqual(["c3", "c4", "c5"]);
  });

  it("keeps everything when it cannot tell whether the item is reviewable", async () => {
    const { d, stored } = deps({ reviewable: async () => { throw new ApiError(0, "down"); } });
    expect(await migrateItem("K-1", draft, d)).toEqual({ posted: 0, dropped: null, kept: draft });
    expect(stored.has("K-1")).toBe(false);
  });

  it.each([
    [409, true],
    [422, true],
    [408, false],
    [429, false],
    // The server failing is not the draft refused: it is kept for the next try.
    [500, false],
  ])("a %i on a draft drops it: %s", async (status, drops) => {
    const { d } = deps({ post: async (_id, t) => { if (t.body.startsWith("a.py")) throw new ApiError(status, "no"); } });
    const r = await migrateItem("K-1", draft, d);
    if (drops) expect(r).toEqual({ posted: 2, dropped: { comments: [draft.comments[0]] }, kept: null, why: "refused" });
    else expect(r).toEqual({ posted: 0, dropped: null, kept: draft });
  });
});

describe("migrateAll", () => {
  const store: Record<string, unknown> = { "kraft.draft.K-1": draft, "kraft.other": { comments: [{ file: "z", line: 1, body: "z" }] }, "kraft.draft.K-2": five };

  it("does nothing while read-only", async () => {
    const { d, posted, stored } = deps();
    expect(await migrateAll(Object.keys(store), (k) => store[k], d, true)).toEqual([]);
    expect(posted).toEqual([]);
    expect(stored.size).toBe(0);
  });

  it("ignores other workspaceState keys and goes one item at a time", async () => {
    const post = vi.fn(async () => {});
    const { d } = deps({ post });
    const r = await migrateAll(Object.keys(store), (k) => store[k], d, false);
    expect(r.map((x) => [x.id, x.posted])).toEqual([["K-1", 3], ["K-2", 5]]);
    expect(post).toHaveBeenCalledTimes(8);
  });
});

it("dropMessage lists every comment line for line", () =>
  expect(dropMessage("K-1", draft).split("\n")).toEqual([
    "Kraft dropped 2 local review comments on K-1: it has nothing to review any more. Their text:",
    "a.py:3 (base): x",
    "b.py:1: y",
    "(summary): s",
  ]));

it("dropMessage names the item as it is told to", () =>
  expect(dropMessage("Cache layer", draft).split("\n")[0]).toBe("Kraft dropped 2 local review comments on Cache layer: it has nothing to review any more. Their text:"));

it.each<[DropReason, LegacyDraft, string]>([
  ["refused", { comments: [{ file: "a.py", line: 3, body: "x" }] }, "Kraft dropped 1 local review comment on K-1: Kraft refused them. Their text:"],
  ["unreviewable", { comments: [], summary: "s" }, "Kraft dropped 1 local review comment on K-1: it has nothing to review any more. Their text:"],
])("dropMessage says why: %s", (why, d, first) => expect(dropMessage("K-1", d, why).split("\n")[0]).toBe(first));
