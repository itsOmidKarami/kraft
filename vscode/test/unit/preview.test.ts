import { describe, expect, it } from "vitest";
import { artifactBasename } from "../../src/core/docs";
import { previewItemId, type TabLike } from "../../src/core/preview";

const MD = "mainThreadWebview-markdown.preview";

describe("previewItemId", () => {
  it.each<[string, TabLike | undefined, string[], string | undefined]>([
    ["a gate document Kraft opened", { label: "Preview spec (K-1).md", viewType: MD }, ["K-1"], "K-1"],
    ["another extension's webview", { label: "Preview spec (K-1).md", viewType: "mainThreadWebview-other" }, ["K-1"], undefined],
    ["a text tab with the same label", { label: "Preview spec (K-1).md" }, ["K-1"], undefined],
    ["an id Kraft did not open", { label: "Preview spec (K-2).md", viewType: MD }, ["K-1"], undefined],
    ["the last (<id>).md, in any language", { label: "Vorschau a (b) (K-1).md", viewType: MD }, ["K-1", "b"], "K-1"],
    ["the last of two (<id>).md", { label: "Preview a (b).md (K-1).md", viewType: MD }, ["K-1", "b"], "K-1"],
    ["an id not right before .md", { label: "Preview (K-1) notes.md", viewType: MD }, ["K-1"], undefined],
    ["no active tab", undefined, ["K-1"], undefined],
  ])("%s", (_, tab, opened, want) => {
    expect(previewItemId(tab, new Set(opened))).toBe(want);
  });

  it.each([
    ["a plain title", "Spec"],
    ["parentheses in the title", "Plan (v2) (K-9)"],
    ["slashes, # and ?", "a/b#c?d"],
  ])("reads the id back off the basename of %s", (_, title) => {
    expect(previewItemId({ label: `Preview ${artifactBasename("K-1", title)}`, viewType: MD }, new Set(["K-1", "K-9", "v2"]))).toBe("K-1");
  });
});
