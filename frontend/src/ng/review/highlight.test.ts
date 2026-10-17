import { afterEach, describe, expect, it, vi } from "vitest";
import { HIGHLIGHT_MAX_LINES, highlightFile, highlightLines, type Token } from "./highlight";
import { LANGS } from "./languages";
import { parsePatch } from "./patch";

const classed = (ts: Token[]) => ts.filter((t) => t.cls).map((t) => [t.text.trim(), t.cls]);
const diff = (path: string, body: string) => parsePatch(`diff --git a/${path} b/${path}\n--- a/${path}\n+++ b/${path}\n${body}`)[0];

afterEach(() => vi.restoreAllMocks());

describe("highlightLines", () => {
  it("colours Ruby by its grammar", async () => {
    const [a, b] = (await highlightLines(["class User", '  def name = "x" # hi', "end"], "ruby"))!;
    expect(classed(a)).toEqual(expect.arrayContaining([["class", "kw"], ["User", "name"]]));
    expect(classed(b)).toEqual(expect.arrayContaining([["def", "kw"], ["name", "name"], ["# hi", "com"]]));
    expect(b.filter((t) => t.cls === "str").map((t) => t.text).join("")).toBe('"x"');
  });

  it.each([
    ["perl", "my $x = 1; # c", [["my", "kw"], ["# c", "com"]]],
    ["php", "<?php function f() { return 1; } // c", [["function", "kw"], ["return", "kw"], ["// c", "com"]]],
    ["java", "class A { int f() { return 1; } }", [["class", "kw"], ["A", "name"], ["1", "num"]]],
    ["python", "def f():  # c", [["def", "kw"], ["f", "name"], ["# c", "com"]]],
    ["jinja", "{% for x in y %}", [["for", "kw"]]],
    ["yaml", "max_items: 50000  # cap", [["max_items", "name"], ["50000", "num"], ["# cap", "com"]]],
    ["markdown", "## Embedding cache", [["## Embedding cache", "name"]]],
  ] as const)("colours %s", async (lang, line, want) => {
    const [t] = (await highlightLines([line], lang))!;
    expect(classed(t)).toEqual(expect.arrayContaining(want as unknown as unknown[][]));
  });

  it("leaves an operator as text, though the grammar files it under keyword", async () => {
    const [t] = (await highlightLines(["x = 1 + 2"], "ruby"))!;
    expect(classed(t)).toEqual([["1", "num"], ["2", "num"]]);
  });

  it("carries a block comment across lines", async () => {
    const [, b] = (await highlightLines(["/* a", "b */ x"], "typescript"))!;
    expect(b[0]).toEqual({ text: "b */", cls: "com" });
    expect(b.find((t) => t.text.includes("x"))?.cls).toBeUndefined();
  });

  it("keeps every line's text exactly, and answers an empty block", async () => {
    const lines = ["\tx = 1\r", "a\rb", "y  ", ""];
    const out = (await highlightLines(lines, "python"))!;
    expect(out.map((ts) => ts.map((t) => t.text).join(""))).toEqual(lines);
    expect(await highlightLines([], "python")).toEqual([]);
  });

  it("answers null and warns once when a grammar fails to load", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(LANGS, "zig").mockRejectedValue(new Error("boom"));
    expect(await highlightLines(["x"], "zig")).toBeNull();
    expect(await highlightLines(["x"], "zig")).toBeNull();
    expect(warn).toHaveBeenCalledTimes(1);
  });
});

describe("highlightFile", () => {
  const f = diff("a.rb", "@@ -1,2 +1,2 @@\n def a\n-  1\n+  2\n@@ -10,0 +11,2 @@\n+x = 1\n+y = 2\n");

  it("gives each hunk's old and new side its own tokens", async () => {
    const t = (await highlightFile(f, "ruby"))!;
    expect(t.old.map((h) => h.length)).toEqual([2, 0]);
    expect(t.new.map((h) => h.length)).toEqual([2, 2]);
    expect(classed(t.new[0][0])).toEqual([["def", "kw"], ["a", "name"]]);
  });

  it("colours a file's other hunks when one hunk side is past the line cap", async () => {
    const n = HIGHLIGHT_MAX_LINES + 1;
    const big = `@@ -1,0 +1,${n} @@\n` + "+x\n".repeat(n);
    const t = (await highlightFile(diff("big.rb", big + "@@ -3000,1 +3000,1 @@\n-def a\n+def b\n"), "ruby"))!;
    expect(t.new[0]).toEqual([]);
    expect(classed(t.new[1][0])).toEqual([["def", "kw"], ["b", "name"]]);
  });

  it("does no Shiki work for a block past the line cap", async () => {
    const body = "@@ -1,0 +1,N @@\n".replace("N", String(HIGHLIGHT_MAX_LINES + 1)) + "+x\n".repeat(HIGHLIGHT_MAX_LINES + 1);
    const load = vi.spyOn(LANGS, "ruby");
    const t = (await highlightFile(diff("big.rb", body), "ruby"))!;
    expect(t.new).toEqual([[]]);
    expect(load).not.toHaveBeenCalled();
  });
});

describe("tokenizing", () => {
  it("yields between blocks, so a timer scheduled first fires before the last of 20 blocks finishes", async () => {
    await highlightLines(["x = 1"], "ruby"); // grammar loaded: from here every block is only microtasks apart
    let done = 0;
    let atTimer = -1;
    setTimeout(() => (atTimer = done), 0);
    await Promise.all(Array.from({ length: 20 }, () => highlightLines(["def a", "  1", "end"], "ruby").then(() => done++)));
    expect(atTimer).toBeGreaterThanOrEqual(0);
    expect(atTimer).toBeLessThan(20);
  });
});
