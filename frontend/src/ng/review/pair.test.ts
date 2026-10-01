import { describe, expect, it } from "vitest";
import { pairRows } from "./pair";
import type { Hunk, PatchLine } from "./patch";

const ctx = (o: number, n: number, text = "c"): PatchLine => ({ kind: " ", old: o, new: n, text });
const del = (o: number, text = "d"): PatchLine => ({ kind: "-", old: o, new: null, text });
const add = (n: number, text = "a"): PatchLine => ({ kind: "+", old: null, new: n, text });
const hunk = (lines: PatchLine[]): Hunk => ({ header: "@@", oldStart: 1, newStart: 1, lines });
const nums = (h: Hunk) => pairRows(h).map((r) => [r.left?.old ?? null, r.right?.new ?? null]);

describe("pairRows", () => {
  it("faces the k-th removed line with the k-th added one; extras face nothing", () => {
    expect(nums(hunk([del(1), del(2), add(1), add(2), add(3)]))).toEqual([[1, 1], [2, 2], [null, 3]]);
    expect(nums(hunk([del(1), del(2), del(3), add(1)]))).toEqual([[1, 1], [2, null], [3, null]]);
  });

  it("restarts the pairing after context, which pairs with itself", () => {
    const h = hunk([ctx(1, 1), del(2), add(2), ctx(3, 3), del(4), add(4), add(5)]);
    expect(nums(h)).toEqual([[1, 1], [2, 2], [3, 3], [4, 4], [null, 5]]);
    expect(pairRows(h)[0].left).toBe(pairRows(h)[0].right);
  });

  it("puts a pure addition on the right only", () => {
    expect(pairRows(hunk([add(1), add(2)]))).toEqual([{ left: null, right: add(1) }, { left: null, right: add(2) }]);
  });
});
