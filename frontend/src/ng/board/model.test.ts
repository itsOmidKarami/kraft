import { describe, expect, it } from "vitest";
import type { DisplayStatus, WorkItem } from "../../types";
import { DISPLAY_STATUSES } from "../../types/vocab.generated";
import { detail } from "../item/testkit";
import { chainKeys, groupOf, groupsOf, type BoardView } from "./model";

const item = (id: string, display_status: DisplayStatus, over: Partial<WorkItem> = {}): WorkItem =>
  detail({ id, title: `t-${id}`, display_status, updated_at: `2026-09-13T0${id.length % 10}:00:00Z`, ...over });
const view = (over: Partial<BoardView> = {}): BoardView => ({ filter: { q: "", repo: "", chain: "", gate: "", stop: "" }, group: "status", sort: "attention", doneCap: null, ...over });
const labels = (items: WorkItem[], v = view()) => groupsOf(items, v).map((g) => `${g.label}:${g.rows.map((r) => r.id).join(",")}`);

describe("groupOf", () => {
  it("files every display status in its board group", () => {
    const cases: [DisplayStatus, string][] = [
      ["needs_you", "needs"], ["failed", "needs"], ["running", "running"], ["waiting", "running"], ["escalated", "running"],
      ["done", "done"], ["cancelled", "done"],
    ];
    for (const [s, g] of cases) expect(groupOf({ display_status: s, current_node_id: "x" }), s).toBe(g);
  });

  it("files every generated display status, and an unknown one as running", () => {
    for (const s of DISPLAY_STATUSES) expect(["needs", "running", "not_started", "done"], s).toContain(groupOf({ display_status: s, current_node_id: "x" }));
    expect(groupOf({ display_status: "brand_new" as DisplayStatus, current_node_id: "n" })).toBe("running");
  });

  it("tells a never-started item from one paused or blocked mid-chain by its current node", () => {
    expect(groupOf({ display_status: "paused", current_node_id: null })).toBe("not_started");
    expect(groupOf({ display_status: "paused", current_node_id: "implement" })).toBe("needs");
    expect(groupOf({ display_status: "blocked", current_node_id: null })).toBe("not_started");
    expect(groupOf({ display_status: "blocked", current_node_id: "implement" })).toBe("running");
  });

  // R16: the group is the server's display_status, never the raw status beside it.
  it.each([
    ["active with a gate stop: Needs you", { status: "active", display_status: "needs_you", stop: { kind: "gate", node: "review", resume_at: null, reason: null } }, "needs"],
    ["needs_human but paused before it started: Not started", { status: "needs_human", display_status: "paused", current_node_id: null }, "not_started"],
    ["paused but running again: Running", { status: "paused", display_status: "running", current_node_id: "implement" }, "running"],
  ] as [string, Partial<WorkItem>, string][])("%s", (_name, over, group) => {
    expect(groupOf(item("w", over.display_status!, over))).toBe(group);
  });
});

describe("groupsOf", () => {
  it("draws only the status groups that hold rows, in order, and none for an empty board", () => {
    expect(groupsOf([], view())).toEqual([]);
    const g = groupsOf([item("d", "done"), item("n", "needs_you")], view());
    expect(g.map((x) => x.label)).toEqual(["Needs you", "Done"]);
  });

  it("puts Needs you first under Group by Repo and Chain, then one group per key by name", () => {
    const items = [
      item("a", "running", { repo: "/r/zeta", chain_template: "docs_only" }),
      item("b", "needs_you", { repo: "/r/zeta" }),
      item("c", "done", { repo: "/r/alpha" }),
    ];
    expect(labels(items, view({ group: "repo" }))).toEqual(["Needs you:b", "alpha:c", "zeta:a"]);
    expect(labels(items, view({ group: "chain" }))).toEqual(["Needs you:b", "default:c", "docs_only:a"]);
    expect(labels([items[0]], view({ group: "repo" }))).toEqual(["zeta:a"]);
  });

  it("sorts by attention, updated, created and title", () => {
    const a = item("a", "running", { title: "Bravo", updated_at: "2026-09-13T03:00:00Z", created_at: "2026-09-10T00:00:00Z" });
    const b = item("b", "needs_you", { title: "Charlie", updated_at: "2026-09-13T01:00:00Z", created_at: "2026-09-12T00:00:00Z" });
    const c = item("c", "running", { title: "alpha", updated_at: "2026-09-13T02:00:00Z", created_at: "2026-09-11T00:00:00Z" });
    const flat = (sort: BoardView["sort"]) => groupsOf([a, b, c], view({ group: "chain", sort, filter: { q: "", repo: "", chain: "" } })).flatMap((g) => g.rows.map((r) => r.id)).join("");
    expect(flat("attention")).toBe("bac");
    const all = (sort: BoardView["sort"]) => groupsOf([a, b, c].map((x) => ({ ...x, display_status: "running" as const })), view({ group: "chain", sort }))[0].rows.map((r) => r.id).join("");
    expect(all("updated")).toBe("acb");
    expect(all("created")).toBe("bca");
    expect(all("title")).toBe("cab");
  });

  it("filters on title, id, bead id and repo, and narrows by repo and chain exactly", () => {
    const items = [item("abc", "running", { title: "Cache layer", bead_id: "kraft-cb59", repo: "/r/kraft-api" }), item("def", "running", { repo: "/r/other", chain_template: "docs_only", bead_id: null })];
    const ids = (f: Partial<BoardView["filter"]>) => groupsOf(items, view({ filter: { q: "", repo: "", chain: "", ...f } })).flatMap((g) => g.rows).map((r) => r.id);
    expect(ids({ q: "CACHE" })).toEqual(["abc"]);
    expect(ids({ q: "cb59" })).toEqual(["abc"]);
    expect(ids({ q: "kraft-api" })).toEqual(["abc"]);
    expect(ids({ q: "de" })).toEqual(["def"]);
    expect(ids({ repo: "/r/other" })).toEqual(["def"]);
    expect(ids({ chain: "default" })).toEqual(["abc"]);
  });

  it("narrows by the gate an item waits at and by its kind of stop, on items that need a person", () => {
    const gate = (id: string, at: string) => item(id, "needs_you", { pending_gate: at, stop: { kind: "gate", node: at, resume_at: null, reason: null } });
    const items = [
      gate("g1", "plan_approval"),
      gate("g2", "spec_approval"),
      item("q1", "needs_you", { stop: { kind: "question", node: "implement", resume_at: null, reason: "x" } }),
      // A run that merely sits at a gate node, or a done item with a stale stop, is neither.
      item("r1", "running", { pending_gate: "plan_approval", stop: { kind: "gate", node: "plan_approval", resume_at: null, reason: null } }),
    ];
    const ids = (f: Partial<BoardView["filter"]>) => groupsOf(items, view({ filter: { q: "", repo: "", chain: "", ...f } })).flatMap((g) => g.rows).map((r) => r.id);
    expect(ids({ gate: "plan_approval" })).toEqual(["g1"]);
    expect(ids({ stop: "gate" }).sort()).toEqual(["g1", "g2"]);
    expect(ids({ stop: "question" })).toEqual(["q1"]);
    expect(ids({ gate: "plan_approval", stop: "question" })).toEqual([]);
  });

  it("caps Done at the newest show_done rows and keeps the total for show all", () => {
    // Titles run against recency, so the Title sort's first two are the oldest.
    const done = ["1", "22", "333", "4444"].map((id, n) => item(id, "done", { title: "abcd"[n] }));
    const g = groupsOf(done, view({ doneCap: 2, sort: "title" }))[0];
    expect(g.total).toBe(4);
    expect(g.rows.map((r) => r.id)).toEqual(["333", "4444"]);
    expect(groupsOf(done, view({ doneCap: null }))[0].rows).toHaveLength(4);
    expect(groupsOf(done, view({ group: "repo", doneCap: 2 }))[0].rows).toHaveLength(4);
  });

  it("reads a one-off chain as one-off, in its place among the labels, and keeps the raw key to filter by", () => {
    const items = [
      item("a", "running", { chain_template: "quick-task" }),
      item("b", "running", { chain_template: "@one-off" }),
      item("c", "running", { chain_template: "default" }),
    ];
    expect(chainKeys(items)).toEqual(["default", "@one-off", "quick-task"]);
    expect(labels(items, view({ group: "chain" }))).toEqual(["default:c", "one-off:b", "quick-task:a"]);
    expect(groupsOf(items, view({ filter: { q: "", repo: "", chain: "@one-off", gate: "", stop: "" } })).flatMap((g) => g.rows.map((r) => r.id))).toEqual(["b"]);
  });
});
