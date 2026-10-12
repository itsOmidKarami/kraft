import * as vscode from "vscode";
import { previewItemId } from "./core/preview";

/** Which gate document's markdown preview is the active tab, if Kraft opened
 *  it: sets `kraft.artifactPreview` for the title bar, and `id()` is the
 *  fallback a title-bar command uses for its item. */
export function registerArtifactPreview(context: vscode.ExtensionContext) {
  const opened = new Set<string>();
  let current: string | undefined;
  const recompute = () => {
    const tab = vscode.window.tabGroups.activeTabGroup.activeTab;
    const viewType = tab?.input instanceof vscode.TabInputWebview ? tab.input.viewType : undefined;
    current = previewItemId(tab && { label: tab.label, viewType }, opened);
    void vscode.commands.executeCommand("setContext", "kraft.artifactPreview", current !== undefined);
  };
  context.subscriptions.push(
    vscode.window.tabGroups.onDidChangeTabs(recompute),
    vscode.window.tabGroups.onDidChangeTabGroups(recompute),
  );
  recompute();
  return {
    /** Kraft is about to preview this item's gate document. */
    opening: (id: string) => void opened.add(id),
    id: () => current,
  };
}

export type ArtifactPreview = ReturnType<typeof registerArtifactPreview>;
