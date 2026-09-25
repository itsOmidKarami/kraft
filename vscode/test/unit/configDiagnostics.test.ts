import { describe, expect, it } from "vitest";
import { fromCheck, fromLint, mergeLint } from "../../src/core/configDiagnostics";

const issue = (file: string, line: number, column: number, message: string, extra = {}) =>
  ({ file, chain: null, message, line, column, related: null, ...extra }) as any;

describe("fromCheck", () => {
  it("converts 1-based positions for the edited file", () => {
    expect(fromCheck([issue("/t/chains/a.yaml", 7, 9, "extends no task")], "/t/chains/a.yaml")).toEqual([
      { line: 6, column: 8, message: "extends no task" },
    ]);
  });

  it("puts another file's issue on the first line, naming it", () => {
    const d = fromCheck([issue("/t/chains/b.yaml", 3, 1, "breaks", { chain: "b" })], "/t/library.yaml");
    expect(d).toEqual([{ line: 0, column: 0, message: "b: breaks" }]);
  });

  it("keeps related information", () => {
    const rel = { file: "/t/library.yaml", line: 4, column: 5 };
    expect(fromCheck([issue("/t/chains/a.yaml", 6, 9, "m", { related: rel })], "/t/chains/a.yaml")[0].related).toEqual(rel);
  });
});

describe("mergeLint", () => {
  const lint = fromLint([issue("/t/chains/a.yaml", 2, 1, "x"), issue("/t/chains/b.yaml", 1, 1, "y")]);

  it("sets clean files and clears files that became clean", () => {
    const { set, clear } = mergeLint(new Set(["/t/chains/c.yaml"]), lint, new Set());
    expect([...set.keys()].sort()).toEqual(["/t/chains/a.yaml", "/t/chains/b.yaml"]);
    expect(clear).toEqual(["/t/chains/c.yaml"]);
  });

  it("never touches a dirty file: the check owns it", () => {
    const { set, clear } = mergeLint(new Set(["/t/chains/c.yaml"]), lint, new Set(["/t/chains/a.yaml", "/t/chains/c.yaml"]));
    expect([...set.keys()]).toEqual(["/t/chains/b.yaml"]);
    expect(clear).toEqual([]);
  });
});
