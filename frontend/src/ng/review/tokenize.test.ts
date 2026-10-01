import { describe, expect, it } from "vitest";
import { languageOf, tokenizeLine, tokenizeSide, type Lang, type Token } from "./tokenize";

/** `[text, class]` for the classed tokens only. */
const classed = (ts: Token[]) => ts.filter((t) => t.cls).map((t) => [t.text, t.cls]);
const line = (l: string, lang: Lang) => tokenizeLine(l, lang, null).tokens;

const FIXTURES: [Lang, string][] = [
  ["clike", 'export function hit(key: string) { return cache.get(key) ?? 0x1F; } // miss'],
  ["python", 'def put(self, key, vec):  # bounded at 50_000 entries, "lru"'],
  ["shell", 'if [ -n "$KRAFT_HOME" ]; then echo 3; fi # home'],
  ["yaml", "  max_items: 50000  # cap"],
  ["markdown", "## Embedding cache"],
];

describe("tokenize", () => {
  it("picks a family from the path", () => {
    expect(["a/b.tsx", "x.py", "run.sh", "Dockerfile", "c.yml", "README.md", "x.lock", "Makefile"].map(languageOf)).toEqual([
      "clike", "python", "shell", "shell", "yaml", "markdown", "plain", "plain",
    ]);
  });

  it("gives each family its classes", () => {
    expect(classed(line(FIXTURES[0][1], "clike"))).toEqual([
      ["export", "kw"], ["function", "kw"], ["hit", "name"], ["string", "kw"], ["return", "kw"], ["0x1F", "num"], ["// miss", "com"],
    ]);
    expect(classed(line(FIXTURES[1][1], "python"))).toEqual([["def", "kw"], ["put", "name"], ['# bounded at 50_000 entries, "lru"', "com"]]);
    expect(classed(line(FIXTURES[2][1], "shell"))).toEqual([["if", "kw"], ['"$KRAFT_HOME"', "str"], ["then", "kw"], ["3", "num"], ["fi", "kw"], ["# home", "com"]]);
    expect(classed(line(FIXTURES[3][1], "yaml"))).toEqual([["max_items", "name"], ["50000", "num"], ["# cap", "com"]]);
    expect(classed(line(FIXTURES[4][1], "markdown"))).toEqual([["## Embedding cache", "name"]]);
    expect(classed(line("x1 = y2", "python"))).toEqual([]);
  });

  it("carries a block comment across a side's lines", () => {
    const [a, b, c, d] = tokenizeSide(["const a = 1; /* starts", "still a comment", "ends */ let b = 2;", "let c = 3;"], "clike");
    expect(classed(a).at(-1)).toEqual(["/* starts", "com"]);
    expect(b).toEqual([{ text: "still a comment", cls: "com" }]);
    expect(classed(c)).toEqual([["ends */", "com"], ["let", "kw"], ["2", "num"]]);
    expect(classed(d)).toEqual([["let", "kw"], ["3", "num"]]);
  });

  it("carries a python triple-quoted string, and starts each side fresh", () => {
    const side = tokenizeSide(['doc = """first', "def not_code(): 1", 'end""" + x'], "python");
    expect(side[1]).toEqual([{ text: "def not_code(): 1", cls: "str" }]);
    expect(classed(side[2])).toEqual([['end"""', "str"]]);
    // A new hunk is a new call: it starts as code.
    expect(classed(tokenizeSide(["def f(): 1"], "python")[0])).toEqual([["def", "kw"], ["f", "name"], ["1", "num"]]);
  });

  it("colours nothing in plain files", () => {
    expect(line("const x = 1 // no", "plain")).toEqual([{ text: "const x = 1 // no" }]);
  });

  it("never changes the text", () => {
    const extra: [Lang, string][] = [["clike", "`tmpl ${a}` + 'q\\'s' /* x */ y"], ["python", "s = 'unterminated"], ["yaml", "- name: \"x\""], ["markdown", "text <!-- note --> more"]];
    for (const [lang, l] of [...FIXTURES, ...extra]) expect(line(l, lang).map((t) => t.text).join("")).toBe(l);
  });
});
