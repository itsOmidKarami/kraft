import { describe, expect, it } from "vitest";
import { artifactBasename } from "../../src/core/docs";
import { gateCall, previewIsStale, previewOf, previewTarget, previewUnresolved, type GroupLike, type ShownPreview, type TabLike } from "../../src/core/preview";

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

describe("previewTarget", () => {
  const opened = new Map([[SPEC, "K-1"], ["Plan (K-2).md", "K-2"]]);
  const k1: GroupLike["activeTab"] = { label: `[Preview] ${SPEC}`, viewType: MD };
  const k2: GroupLike["activeTab"] = { label: "[Preview] Plan (K-2).md", viewType: MD };
  const diff: GroupLike["activeTab"] = { label: "a.py (K-2)" };
  const other: GroupLike["activeTab"] = { label: "Release Notes", viewType: "mainThreadWebview-other" };
  const TITLE = { scheme: "webview-panel", path: "webview-panel/webview-x" };
  const K1 = { id: "K-1", basename: SPEC };
  it.each<[string, unknown, GroupLike[], ShownPreview | undefined, boolean]>([
    ["title bar on the preview's own group", TITLE, [{ isActive: false, activeTab: k1 }, { isActive: true, activeTab: diff }], K1, false],
    ["title bar on another group whose active tab is not a preview", TITLE, [{ isActive: true, activeTab: k1 }, { isActive: false, activeTab: other }], undefined, true],
    ["title bar with two groups on different previews", TITLE, [{ isActive: true, activeTab: k1 }, { isActive: false, activeTab: k2 }], undefined, true],
    ["title bar with two groups on the same preview", TITLE, [{ isActive: true, activeTab: k1 }, { isActive: false, activeTab: k1 }], K1, false],
    ["palette, no argument: the active group's preview", undefined, [{ isActive: false, activeTab: k2 }, { isActive: true, activeTab: k1 }], K1, false],
    ["palette while the active group shows no preview", undefined, [{ isActive: true, activeTab: diff }, { isActive: false, activeTab: k1 }], undefined, false],
    ["a non-preview argument (another group's diff)", { scheme: "kraft-wt", path: "/K-2/a.py" }, [{ isActive: true, activeTab: k1 }], undefined, false],
    ["a board node", { item: { id: "K-3" } }, [{ isActive: true, activeTab: k1 }], undefined, false],
  ])("%s", (_, arg, groups, want, unresolved) => {
    expect(previewTarget(arg, groups, opened)).toEqual(want);
    // A title-bar click that names no preview says so, rather than doing nothing.
    expect(previewUnresolved(arg, previewTarget(arg, groups, opened))).toBe(unresolved);
  });
});

describe("gateCall", () => {
  const latest = new Map([["K-1", "Plan (K-1).md"]]);
  const stalePreview = { id: "K-1", basename: SPEC };
  const freshPreview = { id: "K-1", basename: "Plan (K-1).md" };
  it.each<[string, string | undefined, ShownPreview | undefined, unknown, unknown, ReturnType<typeof gateCall>]>([
    ["the title bar's { groupId } is no gate", undefined, freshPreview, { groupId: 1 }, undefined, { id: "K-1", gate: undefined, note: undefined, stale: false }],
    ["a gate and note given as strings", "K-1", undefined, "spec", "why", { id: "K-1", gate: "spec", note: "why", stale: false }],
    ["a note that is not a string", "K-1", undefined, "spec", { groupId: 1 }, { id: "K-1", gate: "spec", note: undefined, stale: false }],
    ["a stale preview reads first (Approve and Reject alike)", undefined, stalePreview, { groupId: 1 }, undefined, { id: "K-1", gate: undefined, note: undefined, stale: true }],
    ["an item named directly is never stale", "K-1", stalePreview, undefined, undefined, { id: "K-1", gate: undefined, note: undefined, stale: false }],
    ["no item anywhere", undefined, undefined, undefined, undefined, { id: undefined, gate: undefined, note: undefined, stale: false }],
  ])("%s", (_, direct, target, gate, note, want) => {
    expect(gateCall(direct, target, latest, gate, note)).toEqual(want);
  });
});
