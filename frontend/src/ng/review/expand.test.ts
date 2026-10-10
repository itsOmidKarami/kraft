import { describe, expect, it } from "vitest";
import { gapsOf, grow, spansOf, STEP, widen, type Grow, type Span } from "./expand";
import { parsePatch } from "./patch";

// A 60-line file with lines 10 and 50 changed: one diff line per file line, and one more for each change.
const CHANGED = [10, 50];
const body = (a: number, b: number) =>
  Array.from({ length: b - a + 1 }, (_, i) => a + i).map((n) => (CHANGED.includes(n) ? `-old ${n}\n+new ${n}` : ` line ${n}`)).join("\n");
const fileOf = (hunks: [number, number][]) =>
  parsePatch(`diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n${hunks.map(([a, b]) => `@@ -${a},${b - a + 1} +${a},${b - a + 1} @@ def f():\n${body(a, b)}\n`).join("")}`)[0];
const pf = fileOf([[7, 13], [47, 53]]);
const whole = spansOf(pf, fileOf([[1, 60]]))!;

describe("a diff's unchanged lines", () => {
  it("finds each hunk in the whole file, and counts what sits before each and after the last", () => {
    expect(whole.lines).toHaveLength(62);
    expect(whole.spans).toEqual([[6, 14], [47, 55]]);
    // Three unchanged lines follow the last change, as many as git gives: more may follow. Two: the file ends.
    expect(gapsOf(pf)).toEqual([{ from: 1, count: 6 }, { from: 14, count: 33 }, { from: 54, count: null }]);
    expect(gapsOf(fileOf([[47, 52]]))[1]).toEqual({ from: 53, count: 0 });
    expect(spansOf(pf, fileOf([[1, 5]]))).toBeNull();
  });

  it.each<[string, [number, Grow][], Span[]]>([
    ["up shows a step above the hunk", [[1, "up"]], [[6, 14], [47 - STEP, 55]]],
    ["down shows a step below the hunk before", [[1, "down"]], [[6, 14 + STEP], [47, 55]]],
    ["all joins the two hunks", [[1, "all"]], [[6, 55]]],
    ["up and down that meet join them too", [[1, "down"], [1, "up"]], [[6, 55]]],
    ["the first gap opens to the file's first line", [[0, "up"]], [[0, 14], [47, 55]]],
    ["the last opens to its end, and no further", [[2, "down"]], [[6, 14], [47, 62]]],
    ["the last opens whole", [[2, "all"]], [[6, 14], [47, 62]]],
    ["a press for a gap that has closed changes nothing", [[1, "all"], [2, "down"]], [[6, 55]]],
  ])("%s", (_, presses, spans) => {
    expect(presses.reduce((s, [gap, how]) => grow(s, gap, how, whole.lines.length), whole.spans)).toEqual(spans);
  });

  it("draws the wider hunk with its own header, and leaves an untouched one as it was", () => {
    const wide = widen(pf, { lines: whole.lines, spans: grow(whole.spans, 1, "up", whole.lines.length) });
    expect(wide.hunks[0]).toBe(pf.hunks[0]);
    expect(wide.hunks[1]).toMatchObject({ header: "@@ -27,27 +27,27 @@", oldStart: 27, newStart: 27 });
    expect(wide.hunks[1].lines[0]).toEqual({ kind: " ", old: 27, new: 27, text: "line 27" });
    expect(gapsOf(wide)).toEqual([{ from: 1, count: 6 }, { from: 14, count: 13 }, { from: 54, count: 7 }]);
    expect(gapsOf(widen(pf, { lines: whole.lines, spans: [[0, 62]] }))).toEqual([{ from: 1, count: 0 }, { from: 61, count: 0 }]);
  });
});
