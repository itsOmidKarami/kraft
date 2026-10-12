import * as vscode from "vscode";
import { previewOf, previewTarget, type ShownPreview, type TabLike } from "./core/preview";

/** Which gate document's markdown preview is the active tab, if Kraft opened
 *  it: sets `kraft.artifactPreview` for the title bar, and `target(arg)` is the
 *  fallback item for a command whose argument names none. */
export function registerArtifactPreview(context: vscode.ExtensionContext) {
  const opened = new Map<string, string>(); // basename → id
  let current: ShownPreview | undefined;
  // Follows the active group's active tab: the title bar a command was clicked in is that group's.
  const tabLike = (tab: vscode.Tab | undefined): TabLike | undefined =>
    tab && { label: tab.label, viewType: tab.input instanceof vscode.TabInputWebview ? tab.input.viewType : undefined };
  const recompute = () => {
    current = previewOf(tabLike(vscode.window.tabGroups.activeTabGroup.activeTab), opened);
    void vscode.commands.executeCommand("setContext", "kraft.artifactPreview", current !== undefined);
  };
  context.subscriptions.push(
    vscode.window.tabGroups.onDidChangeTabs(recompute),
    vscode.window.tabGroups.onDidChangeTabGroups(recompute),
  );
  recompute();
  return {
    /** Kraft is about to preview this item's gate document under this basename. */
    opening: (id: string, basename: string) => void opened.set(basename, id),
    /** The preview a command's argument points at: see previewTarget. */
    target: (arg: unknown) =>
      previewTarget(arg, vscode.window.tabGroups.all.map((g) => ({ isActive: g.isActive, activeTab: tabLike(g.activeTab) })), opened),
  };
}

export type ArtifactPreview = ReturnType<typeof registerArtifactPreview>;
