import { describe, expect, it } from "vitest";
import { MAX_LINE, changedRanges } from "./words";

const cut = (s: string, r: [number, number][]) => r.map(([a, b]) => s.slice(a, b));

describe("changedRanges", () => {
  it("marks the one changed word on each side", () => {
    const a = "    def __init__(self, max_items=None):";
    const b = "    def __init__(self, max_items=50_000):";
    const r = changedRanges(a, b);
    expect(cut(a, r.old)).toEqual(["None"]);
    expect(cut(b, r.new)).toEqual(["50_000"]);
  });

  it("marks a changed tail, and an insertion only on the side that has it", () => {
    const r = changedRanges("key = content_hash(doc.text)", "key = content_hash(doc.raw)  # raw bytes");
    expect(cut("key = content_hash(doc.raw)  # raw bytes", r.new)).toEqual(["raw)  # raw bytes"]);
    const ins = changedRanges("call(a, b)", "call(a, x, b)");
    expect(ins.old).toEqual([]);
    expect(cut("call(a, x, b)", ins.new)).toEqual(["x, "]);
  });

  it("gives nothing for a rewrite, an identical pair or a long line", () => {
    expect(changedRanges("return self._store.get(key) or None", "if vec is not None: move_to_end(key)")).toEqual({ old: [], new: [] });
    expect(changedRanges("same", "same")).toEqual({ old: [], new: [] });
    const long = "x = 1 ".repeat(MAX_LINE / 5);
    expect(changedRanges(long, long + "y")).toEqual({ old: [], new: [] });
  });
});
