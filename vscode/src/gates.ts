import * as vscode from "vscode";
import { reportError } from "./actions";
import type { Api } from "./core/api";
import { Announcer } from "./core/announcer";
import { announceable, type Include } from "./core/board";
import { approveGate } from "./core/approve";
import { mutating } from "./core/guard";
import type { WorkspaceScope } from "./core/repoScope";
import { commandItemId } from "./core/review";
import type { Store } from "./core/store";

export function registerGates(
  context: vscode.ExtensionContext,
  store: Store,
  api: Api,
  refusal: () => string | null,
  include: Include,
  scope: Pick<WorkspaceScope, "onChange" | "showAll">,
  confirmApprove: (id: string) => Promise<boolean> = async () => true,
) {
  const announcer = new Announcer(() => vscode.workspace.getConfiguration("kraft").get("notifications", "all"));
  const artifacts = new Map<string, string>(); // uri path → markdown

  context.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider("kraft-artifact", {
      provideTextDocumentContent: (uri) => artifacts.get(uri.path) ?? "",
    }),
  );

  const idOf = (arg: unknown) => commandItemId(arg, vscode.window.activeTextEditor?.document.uri, ["kraft-artifact"]);
  const gateOf = (id: string) => store.item(id)?.pending_gate ?? undefined;
  const warn = (msg: string) => void vscode.window.showWarningMessage(msg);

  const openArtifact = async (id?: string) => {
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

  // Guarded here, not at registration: a gate notification's buttons call these directly.
  const approve = mutating("kraft.approve", refusal, warn, async (id?: string, gate?: string) => {
    if (!id) return;
    gate ??= gateOf(id);
    if (!gate) return void vscode.window.showInformationMessage(`${id} is not waiting at a gate.`);
    if (!(await confirmApprove(id))) return; // Task 7: discard review drafts first
    try {
      await approveGate(api, id, gate);
      await store.refresh(id);
    } catch (e) {
      await reportError(e, store, id);
    }
  });

  const reject = mutating("kraft.reject", refusal, warn, async (id?: string, gate?: string, note?: string) => {
    if (!id) return;
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
  });

  const announce = (ids: string[] | "all", quiet = false) => {
    const items = announceable(ids === "all" ? store.items() : ids.map((id) => store.item(id)).filter((i) => i !== undefined), include);
    for (const [item, a] of announcer.considerAll(items, quiet)) {
      if (a.kind === "gate") {
        const buttons = ["Open", "Approve", "Reject"];
        void vscode.window.showInformationMessage(`${item.id} · ${a.gate}: ${item.title}`, ...buttons).then((pick) => {
          if (pick === "Open") void openArtifact(item.id);
          if (pick === "Approve") void approve(item.id, a.gate);
          if (pick === "Reject") void reject(item.id, a.gate);
        });
      } else {
        void vscode.window.showInformationMessage(`${item.id}: ${item.title} — ${a.text}`, "Open in Web UI", "Retry").then((pick) => {
          if (pick === "Open in Web UI") void vscode.commands.executeCommand("kraft.openInWebUi", { item });
          if (pick === "Retry") void vscode.commands.executeCommand("kraft.retry", { item });
        });
      }
    }
  };
  store.onChange(announce);
  // A scope that became ready announces what already waits in it, once (the
  // announcer dedups). Turning on Show all repos only records what it adds:
  // those stops were waiting before (amendment 7). Items out of scope are not
  // considered, so a stop that changed while out of scope reads as new.
  let showAll = scope.showAll;
  scope.onChange(() => {
    const widened = scope.showAll && !showAll;
    showAll = scope.showAll;
    announce("all", widened);
  });

  context.subscriptions.push(
    vscode.commands.registerCommand("kraft.openArtifact", (arg?: unknown) => openArtifact(idOf(arg))),
    vscode.commands.registerCommand("kraft.approve", (arg?: unknown, gate?: string) => approve(idOf(arg), gate)),
    vscode.commands.registerCommand("kraft.reject", (arg?: unknown, gate?: string, note?: string) => reject(idOf(arg), gate, note)),
  );
  return { announcer };
}
