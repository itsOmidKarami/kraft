import { describe, expect, it } from "vitest";
import { artifactBasename } from "../../src/core/docs";
import { previewIsStale, previewOf, type ShownPreview, type TabLike } from "../../src/core/preview";

const MD = "mainThreadWebview-markdown.preview";
const SPEC = "Spec (K-1).md";

describe("previewOf", () => {
  it.each<[string, TabLike | undefined, [string, string][], ShownPreview | undefined]>([
    ["a gate document Kraft opened", { label: `Preview ${SPEC}`, viewType: MD }, [[SPEC, "K-1"]], { id: "K-1", basename: SPEC }],
    ["the locked preview's label", { label: `[Preview] ${SPEC}`, viewType: MD }, [[SPEC, "K-1"]], { id: "K-1", basename: SPEC }],
    ["in any language", { label: `Vorschau ${SPEC}`, viewType: MD }, [[SPEC, "K-1"]], { id: "K-1", basename: SPEC }],
    ["another extension's webview", { label: `Preview ${SPEC}`, viewType: "mainThreadWebview-other" }, [[SPEC, "K-1"]], undefined],
    ["a text tab with the same label", { label: `Preview ${SPEC}` }, [[SPEC, "K-1"]], undefined],
    ["a document Kraft did not open", { label: "Preview Spec (K-2).md", viewType: MD }, [[SPEC, "K-1"]], undefined],
    ["another file whose name ends (K-1).md", { label: "Preview notes (K-1).md", viewType: MD }, [[SPEC, "K-1"]], undefined],
    ["a name that only ends with a basename", { label: `Preview My${SPEC}`, viewType: MD }, [[SPEC, "K-1"]], undefined],
    ["the longest basename the label ends with", { label: `Preview Old ${SPEC}`, viewType: MD }, [[`Old ${SPEC}`, "K-1"], [SPEC, "K-1"]], { id: "K-1", basename: `Old ${SPEC}` }],
    ["no active tab", undefined, [[SPEC, "K-1"]], undefined],
  ])("%s", (_, tab, opened, want) => {
    expect(previewOf(tab, new Map(opened))).toEqual(want);
  });

  it.each([
    ["a plain title", "Spec"],
    ["parentheses in the title", "Plan (v2) (K-9)"],
    ["slashes, # and ?", "a/b#c?d"],
  ])("reads the item back off the basename of %s", (_, title) => {
    const basename = artifactBasename("K-1", title);
    expect(previewOf({ label: `Preview ${basename}`, viewType: MD }, new Map([[basename, "K-1"]]))).toEqual({ id: "K-1", basename });
  });
});

describe("previewIsStale", () => {
  it.each<[string, string | undefined, boolean]>([
    ["the latest render's basename", SPEC, false],
    ["a stale basename of the same id: read first", "Plan (K-1).md", true],
    ["no render remembered", undefined, true],
  ])("%s", (_, latest, want) => {
    expect(previewIsStale({ id: "K-1", basename: SPEC }, new Map(latest ? [["K-1", latest]] : []))).toBe(want);
  });
});
