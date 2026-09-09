import { describe, expect, it } from "vitest";
import { lineDiff } from "./diff";

const tags = (out: { tag: string; text: string }[]) => out.map((l) => l.tag + l.text);

describe("lineDiff", () => {
  it("marks an inserted line and keeps its neighbours as context", () => {
    expect(tags(lineDiff("a\nc", "a\nb\nc"))).toEqual([" a", "+b", " c"]);
  });

  it("marks a deleted line", () => {
    expect(tags(lineDiff("a\nb\nc", "a\nc"))).toEqual([" a", "-b", " c"]);
  });

  it("shows a modified line as a delete and an insert", () => {
    expect(tags(lineDiff("a\nb\nc", "a\nB\nc"))).toEqual([" a", "-b", "+B", " c"]);
  });

  it("reports no change at all for identical input", () => {
    // Every line is context; the caller renders "no unsaved changes" off the
    // strings being equal, so this is the invariant, not an empty array.
    expect(lineDiff("a\nb", "a\nb").filter((l) => l.tag !== " ")).toEqual([]);
  });
});
