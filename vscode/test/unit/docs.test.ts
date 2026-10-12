import { describe, expect, it } from "vitest";
import { docPath, docText } from "../../src/core/docs";

describe("docText", () => {
  it.each([
    ["whole, as served", false, "# S\n"],
    ["truncated, with the footer", true, "# S\n\n\n---\n*Truncated: open it in the web UI for the whole document.*\n"],
  ])("%s", (_, truncated, want) => {
    expect(docText("# S\n", truncated)).toBe(want);
  });
});

describe("docPath", () => {
  it.each([
    ["a plain title", "K-1/spec", "Spec", "/K-1/spec/Spec.md"],
    ["slashes in the title stay one segment", "K-1/plan", "a/b\\c", "/K-1/plan/a-b-c.md"],
    ["# ? % kept as written", "K-1/spec", "50% #1?", "/K-1/spec/50% #1?.md"],
  ])("%s", (_, key, title, want) => {
    expect(docPath(key, title)).toBe(want);
  });
});
