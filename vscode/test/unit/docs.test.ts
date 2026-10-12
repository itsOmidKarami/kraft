import { describe, expect, it } from "vitest";
import { artifactBasename, artifactUri, docPath, docText } from "../../src/core/docs";

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

describe("artifactBasename", () => {
  it.each([
    ["a plain title", "Spec", "Spec (K-1).md"],
    ["parentheses and slashes made dashes", "Plan (v2) a/b\\c", "Plan -v2- a-b-c (K-1).md"],
    ["# and ? kept, the id last", "a#b?c", "a#b?c (K-1).md"],
  ])("%s", (_, title, want) => {
    expect(artifactBasename("K-1", title)).toBe(want);
  });
});

describe("artifactUri", () => {
  it.each([
    ["a plain title", "Spec", "/K-1/Spec (K-1).md"],
    ["# and ? kept in the path", "a#b?c", "/K-1/a#b?c (K-1).md"],
  ])("%s", (_, title, path) => {
    expect(artifactUri("K-1", title)).toEqual({ scheme: "kraft-artifact", path });
  });
});
