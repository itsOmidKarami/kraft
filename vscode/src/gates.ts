import * as vscode from "vscode";
import { reportError } from "./actions";
import type { Api } from "./core/api";
import { Announcer } from "./core/announcer";
import { approveGate } from "./core/approve";
import type { Store } from "./core/store";

export function registerGates(
  context: vscode.ExtensionContext,
  store: Store,
  api: Api,
  readOnly: () => boolean,
  confirmApprove: (id: string) => Promise<boolean> = async () => true,
) {
  const announcer = new Announcer(() => vscode.workspace.getConfiguration("kraft").get("notifications", "all"));
  const artifacts = new Map<string, string>(); // uri path → markdown

  context.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider("kraft-artifact", {
      provideTextDocumentContent: (uri) => artifacts.get(uri.path) ?? "",
    }),
  );

  const idOfEditor = () => {
    const uri = vscode.window.activeTextEditor?.document.uri;
    return uri?.scheme === "kraft-artifact" ? uri.path.split("/")[1] : undefined;
  };
  const gateOf = (id: string) => store.item(id)?.pending_gate ?? undefined;
  const guard = () => {
    if (readOnly()) void vscode.window.showWarningMessage("Kraft is read-only: the daemon's version does not match this extension.");
    return !readOnly();
  };

  const openArtifact = async (id?: string) => {
    id ??= idOfEditor();
    if (!id) return;
    try {
      const a = await api.getArtifact(id);
      if (!a) return void vscode.window.showInformationMessage(`${id}'s gate has no document to show.`);
      const path = `/${id}/${a.title.replace(/[\\/]/g, "-")}.md`;
      artifacts.set(path, a.truncated ? `${a.content}\n\n---\n*Truncated: open it in the web UI for the whole document.*\n` : a.content);
      const doc = await vscode.workspace.openTextDocument(vscode.Uri.parse(`kraft-artifact:${path}`));
      await vscode.window.showTextDocument(doc, { preview: false });
    } catch (e) {
      await reportError(e, store, id);
    }
  };

  const approve = async (id?: string, gate?: string) => {
    id ??= idOfEditor();
    if (!id || !guard()) return;
    gate ??= gateOf(id);
    if (!gate) return void vscode.window.showInformationMessage(`${id} is not waiting at a gate.`);
    if (!(await confirmApprove(id))) return; // Task 7: discard review drafts first
    try {
      await approveGate(api, id, gate);
      await store.refresh(id);
    } catch (e) {
      await reportError(e, store, id);
    }
  };

  const reject = async (id?: string, gate?: string, note?: string) => {
    id ??= idOfEditor();
    if (!id || !guard()) return;
    gate ??= gateOf(id);
    if (!gate) return void vscode.window.showInformationMessage(`${id} is not waiting at a gate.`);
    note ??= await vscode.window.showInputBox({
      prompt: `Why reject ${id} at ${gate}?`,
      validateInput: (v) => (v.trim() ? undefined : "A reject needs a reason: it goes to the agent."),
    });
    if (!note?.trim()) return;
    try {
      await api.reject(id, gate, note.trim());
      await store.refresh(id);
    } catch (e) {
      await reportError(e, store, id);
    }
  };

  const announce = (ids: string[] | "all") => {
    const items = ids === "all" ? store.items() : ids.map((id) => store.item(id)).filter((i) => i !== undefined);
    for (const item of items) {
      const a = announcer.consider(item);
      if (!a) continue;
      if (a.kind === "gate") {
        const buttons = ["Open", "Approve", "Reject"];
        void vscode.window.showInformationMessage(`${item.id} · ${a.gate}: ${item.title}`, ...buttons).then((pick) => {
          if (pick === "Open") void openArtifact(item.id);
          if (pick === "Approve") void approve(item.id, a.gate);
          if (pick === "Reject") void reject(item.id, a.gate);
        });
      } else {
        const action = a.state === "paused" ? "Resume" : "Retry";
        void vscode.window.showInformationMessage(`${item.id} needs you (${a.state}): ${item.title}`, "Open in Web UI", action).then((pick) => {
          if (pick === "Open in Web UI") void vscode.commands.executeCommand("kraft.openInWebUi", { item });
          if (pick === action) void vscode.commands.executeCommand(`kraft.${action.toLowerCase()}`, { item });
        });
      }
    }
  };
  store.onChange(announce);

  context.subscriptions.push(
    vscode.commands.registerCommand("kraft.openArtifact", (arg?: string | { item?: { id: string } }) => openArtifact(typeof arg === "string" ? arg : arg?.item?.id)),
    vscode.commands.registerCommand("kraft.approve", (arg?: string | { item?: { id: string } }, gate?: string) => approve(typeof arg === "string" ? arg : arg?.item?.id, gate)),
    vscode.commands.registerCommand("kraft.reject", (arg?: string | { item?: { id: string } }, gate?: string, note?: string) => reject(typeof arg === "string" ? arg : arg?.item?.id, gate, note)),
  );
  return { announcer };
}
