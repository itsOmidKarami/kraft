import { describe, expect, it } from "vitest";
import type { ReviewComment, ReviewThread } from "../../src/core/api";
import {
  authorName, clampLines, commentContext, commentMarkdown, commentPatch, discardPlan, fitPlace, otherThreadsText, placeKey, placeThread,
  selectionLines, sideOfScheme, syncPlan, threadContext, threadLabel, toThreadIn, writeTarget, type Lines, type Place, type WriteTarget,
} from "../../src/core/threads";
import { C, T } from "./fixtures/thread";

const shown = { files: ["src/a.py", "gone.py", "new/name.py"], renames: new Map([["new/name.py", "old/name.py"]]) };

describe("placeThread", () => {
  it.each<{ id: string; t: Partial<ReviewThread>; place: Place }>([
    { id: "new side, one line", t: { side: "new", start_line: 5, end_line: 5 }, place: { kind: "file", file: "src/a.py", side: "new", lines: { start: 4, end: 4 } } },
    { id: "base side range", t: { side: "old", start_line: 2, end_line: 4 }, place: { kind: "file", file: "src/a.py", side: "old", lines: { start: 1, end: 3 } } },
    { id: "deleted file, old side", t: { file_path: "gone.py", side: "old", start_line: 1, end_line: 1 }, place: { kind: "file", file: "gone.py", side: "old", lines: { start: 0, end: 0 } } },
    { id: "file comment, no lines", t: { side: null, start_line: null, end_line: null }, place: { kind: "file", file: "src/a.py", side: "new", lines: { start: 0, end: 0 } } },
    { id: "across sides", t: { side: "new", start_side: "old", start_line: 3, end_line: 7 }, place: { kind: "file", file: "src/a.py", side: "new", lines: { start: 6, end: 6 } } },
    { id: "no file", t: { file_path: null, side: null, start_line: null, end_line: null }, place: { kind: "whole" } },
    { id: "end_line missing", t: { side: "new", start_line: 4, end_line: null }, place: { kind: "file", file: "src/a.py", side: "new", lines: { start: 3, end: 3 } } },
    { id: "a file not in this review", t: { file_path: "other.py" }, place: { kind: "elsewhere" } },
    { id: "renamed, old side: the old path's left document", t: { file_path: "new/name.py", side: "old", start_line: 2, end_line: 2 }, place: { kind: "file", file: "old/name.py", side: "old", lines: { start: 1, end: 1 } } },
    { id: "renamed, new side: the new path", t: { file_path: "new/name.py", side: "new", start_line: 2, end_line: 2 }, place: { kind: "file", file: "new/name.py", side: "new", lines: { start: 1, end: 1 } } },
  ])("$id", ({ t, place }) => expect(placeThread(T(t), shown)).toEqual(place));
});

describe("fitPlace", () => {
  const doc = (lines: string[]) => ({ lineCount: lines.length, lineAt: (n: number) => lines[n] });
  const at = (start: number, end: number): Place => ({ kind: "file", file: "src/a.py", side: "new", lines: { start, end } });
  it.each<{ id: string; t: Partial<ReviewThread>; place: Place; lines: string[]; want: Place }>([
    { id: "a quote that still reads stays", t: { quote: "+b\n c" }, place: at(1, 2), lines: ["a", "b", "c"], want: at(1, 2) },
    { id: "a quote that no longer reads goes elsewhere", t: { quote: "+b\n c" }, place: at(1, 2), lines: ["a", "x", "c"], want: { kind: "elsewhere" } },
    { id: "a clipped quote compares as a prefix", t: { quote: "+bee…" }, place: at(1, 1), lines: ["a", "beetle"], want: at(1, 1) },
    { id: "a quote cut at its line limit compares what it kept", t: { quote: "+b\n…" }, place: at(1, 2), lines: ["a", "b", "zzz"], want: at(1, 2) },
    { id: "no quote", t: { quote: null }, place: at(1, 1), lines: ["a", "b"], want: at(1, 1) },
    { id: "across sides is not compared", t: { side: "new", start_side: "old", quote: "-x\n+y" }, place: at(1, 1), lines: ["a", "b"], want: at(1, 1) },
    { id: "past the end: drawn on the last line", t: { quote: "+zz" }, place: at(9, 11), lines: ["a", "b"], want: at(1, 1) },
    { id: "whole stays whole", t: {}, place: { kind: "whole" }, lines: [], want: { kind: "whole" } },
  ])("$id", ({ t, place, lines, want }) => expect(fitPlace(T(t), place, doc(lines))).toEqual(want));
});

it.each<[Lines, number, Lines]>([
  [{ start: 10, end: 12 }, 5, { start: 4, end: 4 }],
  [{ start: 1, end: 9 }, 5, { start: 1, end: 4 }],
  [{ start: 0, end: 0 }, 0, { start: 0, end: 0 }],
])("clampLines(%j, %i)", (l, n, want) => expect(clampLines(l, n)).toEqual(want));

it.each([
  [{ line: 2, character: 0 }, { line: 5, character: 0 }, { start: 2, end: 4 }],
  [{ line: 2, character: 3 }, { line: 2, character: 3 }, { start: 2, end: 2 }],
  [{ line: 2, character: 0 }, { line: 5, character: 1 }, { start: 2, end: 5 }],
])("selectionLines(%j, %j)", (s, e, want) => expect(selectionLines(s, e)).toEqual(want));

it("placeKey tells places apart", () => {
  expect(placeKey({ kind: "file", file: "a", side: "old", lines: { start: 1, end: 2 } })).toBe("a|old|1|2");
  expect(placeKey({ kind: "whole" })).toBe("whole");
  expect(placeKey({ kind: "elsewhere" })).toBe("elsewhere");
});

it.each([
  ["kraft-git", "old"],
  ["kraft-wt", "new"],
  ["kraft-artifact", undefined],
])("sideOfScheme(%s)", (scheme, side) => expect(sideOfScheme(scheme)).toBe(side));

describe("toThreadIn", () => {
  it.each([
    {
      id: "base side, no label", args: ["kraft-git", "src/a.py", { start: 1, end: 2 }, "b", null] as const,
      want: { body: "b", file_path: "src/a.py", side: "old", start_line: 2, end_line: 3 },
    },
    {
      id: "branch side, labelled", args: ["kraft-wt", "src/a.py", { start: 0, end: 0 }, "b", "nit"] as const,
      want: { body: "b", file_path: "src/a.py", side: "new", start_line: 1, end_line: 1, label: "nit" },
    },
    {
      id: "renamed, old side: written on the new path", args: ["kraft-git", "old/name.py", { start: 1, end: 1 }, "b", null] as const,
      want: { body: "b", file_path: "new/name.py", side: "old", start_line: 2, end_line: 2 },
    },
  ])("$id", ({ args, want }) => {
    const got = toThreadIn(args[0], args[1], args[2], args[3], args[4], shown.renames);
    expect(got).toEqual(want);
    for (const k of ["start_side", "quote", "anchor_sha", "suggestion"]) expect(got).not.toHaveProperty(k);
  });

  it("is undefined off a review document", () => expect(toThreadIn("kraft-artifact", "a.py", { start: 0, end: 0 }, "b", null)).toBeUndefined());

  it.each([
    ["kraft-git", "src/a.py", { start: 1, end: 2 }],
    ["kraft-wt", "src/a.py", { start: 0, end: 0 }],
    ["kraft-git", "old/name.py", { start: 3, end: 3 }],
  ] as const)("round-trips %s %s", (scheme, file, lines) => {
    const t = toThreadIn(scheme, file, lines, "b", null, shown.renames)!;
    expect(placeThread(T(t as Partial<ReviewThread>), shown)).toEqual({ kind: "file", file, side: sideOfScheme(scheme), lines });
  });
});

describe("threadLabel", () => {
  it.each<{ id: string; t: Partial<ReviewThread>; want: string | undefined }>([
    { id: "draft must-fix", t: { draft: true, label: "must_fix" }, want: "Draft · Must fix" },
    { id: "submitted claimed question", t: { label: "question", state: "claimed" }, want: "Question · Claimed" },
    { id: "submitted open, no label", t: {}, want: undefined },
    { id: "resolved nit", t: { label: "nit", state: "resolved" }, want: "Nit · Resolved" },
  ])("$id", ({ t, want }) => expect(threadLabel(T(t))).toBe(want));
});

describe("threadContext", () => {
  it.each<{ id: string; t: Partial<ReviewThread>; want: string }>([
    { id: "a draft can be relabelled, never resolved", t: { draft: true }, want: "kraft-thread:can-label" },
    { id: "open submitted", t: {}, want: "kraft-thread:can-resolve" },
    { id: "claimed", t: { state: "claimed" }, want: "kraft-thread:can-resolve" },
    { id: "resolved", t: { state: "resolved" }, want: "kraft-thread:can-reopen" },
  ])("$id", ({ t, want }) => expect(threadContext(T(t))).toBe(want));
});

it.each([
  { id: "your draft", c: { draft: true }, want: "kraft-comment:mine-draft" },
  { id: "your submitted", c: {}, want: "kraft-comment:" },
  { id: "an agent's", c: { author: "implement", review_id: null }, want: "kraft-comment:" },
])("commentContext: $id", ({ c, want }) => expect(commentContext(T(), C(c))).toBe(want));

it.each([
  { c: { draft: true }, want: "You (draft)" },
  { c: {}, want: "You" },
  { c: { author: "implement" }, want: "implement" },
])("authorName $want", ({ c, want }) => expect(authorName(C(c))).toBe(want));

describe("writeTarget", () => {
  const first = C({ id: "c1", draft: true });
  const second = C({ id: "c2", draft: true });
  it.each<{ id: string; t: ReviewThread; c: ReviewComment; want: WriteTarget }>([
    { id: "a draft thread's first comment is the thread", t: T({ draft: true, comments: [first, second] }), c: first, want: { kind: "thread", tid: "t1" } },
    { id: "your draft reply on a submitted thread", t: T({ comments: [C(), second] }), c: second, want: { kind: "comment", cid: "c2" } },
    { id: "your draft second comment on a draft thread", t: T({ draft: true, comments: [first, second] }), c: second, want: { kind: "comment", cid: "c2" } },
  ])("$id", ({ t, c, want }) => expect(writeTarget(t, c)).toEqual(want));
});

it("commentPatch sends the comment's suggestion back", () => {
  const suggestion = { start_line: 1, end_line: 1, replacement: "y" };
  expect(commentPatch(C({ suggestion }), "x")).toEqual({ body: "x", suggestion });
});

describe("commentMarkdown", () => {
  it("fences a replacement holding triple backticks with four", () =>
    expect(commentMarkdown(C({ body: "b", suggestion: { start_line: 2, end_line: 3, replacement: "a ``` b" } }))).toBe(
      "b\n\nSuggested replacement for lines 2-3:\n\n````\na ``` b\n````"));
  it("adds the claim", () => expect(commentMarkdown(C({ body: "done", claim: "fixed" }))).toBe("done\n\n_fixed_"));
  it("is the body alone otherwise", () => expect(commentMarkdown(C({ body: "b" }))).toBe("b"));
});

it("discardPlan deletes draft threads whole and your draft replies one by one, counting every draft", () => {
  const threads = [
    T({ id: "t1", draft: true, comments: [C({ id: "c1", draft: true }), C({ id: "c2", draft: true })] }),
    T({ id: "t2", comments: [C({ id: "c3" }), C({ id: "c_reply_on_t2", draft: true }), C({ id: "c5", author: "implement" })] }),
    T({ id: "t3", comments: [C({ id: "c6" })] }),
  ];
  expect(discardPlan(threads)).toEqual({ threads: ["t1"], comments: ["c_reply_on_t2"], count: 3 });
});

describe("otherThreadsText", () => {
  const threads = [
    T({ id: "a", file_path: "x.py", start_line: 3, comments: [C({ body: "on x\nmore" })] }),
    T({ id: "b", file_path: null, comments: [C({ body: "overall" })] }),
    T({ id: "c", file_path: "y.py", start_line: null, comments: [C({ body: "on y" })] }),
    T({ id: "d", comments: [C({ body: "drawn" })] }),
  ];
  const places = new Map<string, Place>([["a", { kind: "elsewhere" }], ["b", { kind: "whole" }], ["c", { kind: "elsewhere" }]]);

  it("lists the whole change, then the files not in this comparison", () => {
    const { text, lineOf } = otherThreadsText("K-1", threads, places);
    expect(text).toBe([
      "# K-1: threads not on a file in this review", "",
      "## On the whole change", "", "- overall", "",
      "## Threads on files not in this comparison", "", "- x.py:3: on x", "- y.py: on y", "",
    ].join("\n"));
    expect([...lineOf]).toEqual([["b", 4], ["a", 8], ["c", 9]]);
  });

  it("leaves out an empty group's heading", () =>
    expect(otherThreadsText("K-1", threads, new Map([["a", { kind: "elsewhere" }]])).text).not.toContain("On the whole change"));
});

it("syncPlan draws the new, redraws the moved, updates the rest and drops the gone", () => {
  const drawn = new Map([["same", "k1"], ["moved", "k2"], ["gone", "k3"]]);
  const next = [{ id: "same", k: "k1" }, { id: "moved", k: "k9" }, { id: "fresh", k: "k4" }];
  const plan = syncPlan(drawn, next, (t) => t.k);
  expect(plan.create.map((t) => t.id)).toEqual(["moved", "fresh"]);
  expect(plan.update.map((t) => t.id)).toEqual(["same"]);
  expect(plan.remove).toEqual(["moved", "gone"]);
});
