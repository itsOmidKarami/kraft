import * as vscode from "vscode";
import { reportError } from "./actions";
import type { Api } from "./core/api";
import { Announcer } from "./core/announcer";
import { approveOrRead, isStaleRevision } from "./core/approve";
import type { Artifact } from "./core/api";
import { ShownRenders } from "./core/shown";
import { mutating } from "./core/guard";
import { commandItemId } from "./core/review";
import type { Store } from "./core/store";

export function registerGates(
  context: vscode.ExtensionContext,
  store: Store,
  api: Api,
  refusal: () => string | null,
  confirmApprove: (id: string) => Promise<boolean> = async () => true,
) {
  const announcer = new Announcer(() => vscode.workspace.getConfiguration("kraft").get("notifications", "all"));
  const artifacts = new Map<string, string>(); // uri path → markdown
  const shown = new ShownRenders(); // what each item's tab last showed, so Approve binds to it
  const changed = new vscode.EventEmitter<vscode.Uri>();

  context.subscriptions.push(
    changed,
    vscode.workspace.registerTextDocumentContentProvider("kraft-artifact", {
      onDidChange: changed.event,
      provideTextDocumentContent: (uri) => artifacts.get(uri.path) ?? "",
    }),
  );

  const idOf = (arg: unknown) => commandItemId(arg, vscode.window.activeTextEditor?.document.uri, ["kraft-artifact"]);
  const gateOf = (id: string) => store.item(id)?.pending_gate ?? undefined;
  const warn = (msg: string) => void vscode.window.showWarningMessage(msg);

  // Resolves once the tab is up; only then is the render remembered as read.
  const show = async (id: string, gate: string, a: Artifact) => {
    const path = `/${id}/${a.title.replace(/[\\/]/g, "-")}.md`;
    artifacts.set(path, a.truncated ? `${a.content}\n\n---\n*Truncated: open it in the web UI for the whole document.*\n` : a.content);
    const uri = vscode.Uri.parse(`kraft-artifact:${path}`);
    changed.fire(uri); // a tab already open on this path reloads
    const doc = await vscode.workspace.openTextDocument(uri);
    await vscode.window.showTextDocument(doc, { preview: false });
    shown.record(id, gate, a.digest);
  };

  const openArtifact = async (id?: string, gate?: string) => {
    if (!id) return;
    gate ??= gateOf(id);
    try {
      const a = await api.getArtifact(id);
      if (!a) return void vscode.window.showInformationMessage(`${id}'s gate has no document to show.`);
      if (!gate) return void vscode.window.showInformationMessage(`${id} is not waiting at a gate.`);
      await show(id, gate, a);
    } catch (e) {
      await reportError(e, store, id);
    }
  };

  // Guarded here, not at registration: a gate notification's buttons call these directly.
  const approve = mutating("kraft.approve", refusal, warn, async (id?: string, gate?: string) => {
    if (!id) return;
    gate ??= gateOf(id);
    if (!gate) return void vscode.window.showInformationMessage(`${id} is not waiting at a gate.`);
    const g = gate;
    try {
      // confirmApprove discards review drafts, so it runs only once an approve will really be sent.
      const outcome = await approveOrRead(api, shown, id, g, (a) => show(id, g, a), () => confirmApprove(id));
      if (outcome === "read-first") void vscode.window.showInformationMessage(`Read ${g} first: the document is open.`);
      if (outcome === "approved") await store.refresh(id);
    } catch (e) {
      if (isStaleRevision(e)) await openArtifact(id, g);
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
      shown.forget(id);
      await store.refresh(id);
    } catch (e) {
      await reportError(e, store, id);
    }
  });

  const announce = (ids: string[] | "all") => {
    const items = ids === "all" ? store.items() : ids.map((id) => store.item(id)).filter((i) => i !== undefined);
    for (const item of items) {
      const a = announcer.consider(item);
      if (!a) continue;
      if (a.kind === "gate") {
        const buttons = ["Open", "Reject…"];
        void vscode.window.showInformationMessage(`${item.id} · ${a.gate}: ${item.title}`, ...buttons).then((pick) => {
          if (pick === "Open") void openArtifact(item.id, a.gate);
          if (pick === "Reject…") void reject(item.id, a.gate);
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
  store.onChange(() => shown.reconcile((id) => { const i = store.item(id); return i ? (i.pending_gate ?? null) : undefined; }));

  context.subscriptions.push(
    vscode.commands.registerCommand("kraft.openArtifact", (arg?: unknown) => openArtifact(idOf(arg))),
    vscode.commands.registerCommand("kraft.approve", (arg?: unknown, gate?: string) => approve(idOf(arg), gate)),
    vscode.commands.registerCommand("kraft.reject", (arg?: unknown, gate?: string, note?: string) => reject(idOf(arg), gate, note)),
  );
  return { announcer };
}
