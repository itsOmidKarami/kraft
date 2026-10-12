import { describe, expect, it } from "vitest";
import type { ReviewThread } from "../../src/core/api";
import { T } from "./fixtures/thread";
import {
  clampLines, fitPlace, placeKey, placeThread, selectionLines, sideOfScheme, toThreadIn, type Lines, type Place,
} from "../../src/core/threads";

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
