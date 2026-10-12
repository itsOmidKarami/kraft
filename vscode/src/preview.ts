import * as vscode from "vscode";
import { previewOf, type ShownPreview } from "./core/preview";

/** Which gate document's markdown preview is the active tab, if Kraft opened
 *  it: sets `kraft.artifactPreview` for the title bar, and `current()` is the
 *  fallback a title-bar command uses for its item. */
export function registerArtifactPreview(context: vscode.ExtensionContext) {
  const opened = new Map<string, string>(); // basename → id
  let current: ShownPreview | undefined;
  // Follows the active group's active tab: the title bar a command was clicked in is that group's.
  const recompute = () => {
    const tab = vscode.window.tabGroups.activeTabGroup.activeTab;
    const viewType = tab?.input instanceof vscode.TabInputWebview ? tab.input.viewType : undefined;
    current = previewOf(tab && { label: tab.label, viewType }, opened);
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
    current: () => current,
    id: () => current?.id,
  };
}

export type ArtifactPreview = ReturnType<typeof registerArtifactPreview>;
