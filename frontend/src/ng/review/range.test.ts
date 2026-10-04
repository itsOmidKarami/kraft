import { describe, expect, it } from "vitest";
import { rangeLabel, type LineRange } from "./range";

describe("rangeLabel: where a comment goes, as its header and the + tooltip say it (RV-1)", () => {
  it.each<[string, LineRange, string]>([
    ["one new line", { side: "new", start: 5, end: 5 }, "Line 5"],
    ["several new lines", { side: "new", start: 2, end: 10 }, "Lines 2–10"],
    ["one old line", { side: "old", start: 4, end: 4 }, "Old line 4"],
    ["several old lines", { side: "old", start: 4, end: 6 }, "Old lines 4–6"],
    ["old to new", { startSide: "old", start: 5, side: "new", end: 6 }, "Old 5 – new 6"],
    ["new to old", { startSide: "new", start: 5, side: "old", end: 6 }, "New 5 – old 6"],
  ])("%s", (_, range, label) => expect(rangeLabel(range)).toBe(label));
});
