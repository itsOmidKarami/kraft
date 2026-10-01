import { describe, expect, it } from "vitest";
import { folded, gutter, lineDiff } from "./lineDiff";

describe("lineDiff", () => {
  it("keeps common lines and marks the rest", () => {
    expect(lineDiff(["a", "b", "c"], ["a", "x", "c", "d"]).map((o) => `${o.t}${o.s}`)).toEqual([" a", "-b", "+x", " c", "+d"]);
  });

  it("folds unchanged runs to one mark, keeping two lines of context", () => {
    const a = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];
    const b = [...a];
    b[7] = "X";
    expect(folded(lineDiff(a, b)).map((o) => ("s" in o ? `${o.t}${o.s}` : "…"))).toEqual(["…", " 6", " 7", "-8", "+X", " 9"]);
  });

  it("marks added and changed lines for the gutter; a new file is all added", () => {
    expect(gutter("a\nb\nc", "a\nB\nc\nd")).toEqual(["", "~", "", "+"]);
    expect(gutter(null, "a\n\nb")).toEqual(["+", "", "+"]);
  });
});
