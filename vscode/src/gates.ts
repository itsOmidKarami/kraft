import * as vscode from "vscode";
import { drafts } from "../../frontend/src/ng/review/finish";
import { reportError } from "./actions";
import type { Api, WorkItem } from "./core/api";
import { Announcer, type Announcement } from "./core/announcer";
import { announceable, onBoard, type Include } from "./core/board";
import { approveOrRead, isStaleRevision } from "./core/approve";
import { approveFlow } from "./core/mustFix";
import { showRefusal } from "./review/refusal";
import type { Artifact } from "./core/api";
import { ShownRenders } from "./core/shown";
import { mutating } from "./core/guard";
import type { WorkspaceScope } from "./core/repoScope";
import { artifactBasename, docText } from "./core/docs";
import { commandItemId } from "./core/review";
import type { Store } from "./core/store";
import type { ThreadCache } from "./core/threadCache";
import { pickItem } from "./palette";
import type { ArtifactPreview } from "./preview";

export function registerGates(
  context: vscode.ExtensionContext,
  store: Store,
  api: Api,
  refusal: () => string | null,
  include: Include,
  scope: Pick<WorkspaceScope, "onChange" | "showAll">,
  cache: ThreadCache,
  reveal: (id: string, tid: string) => Promise<boolean | undefined>,
  preview: ArtifactPreview,
) {
  const announcer = new Announcer(() => vscode.workspace.getConfiguration("kraft").get("notifications", "all"));
  const artifacts = new Map<string, string>(); // uri path → markdown
  const sources = new Map<string, vscode.Uri>(); // item id → its gate document's last uri
  const shown = new ShownRenders(); // what each item's tab last showed, so Approve binds to it
  const changed = new vscode.EventEmitter<vscode.Uri>();

  context.subscriptions.push(
    changed,
    vscode.workspace.registerTextDocumentContentProvider("kraft-artifact", {
      onDidChange: changed.event,
      provideTextDocumentContent: (uri) => artifacts.get(uri.path) ?? "",
    }),
  );

  // A gate document's preview passes no kraft-artifact uri: its item is the one the active preview shows.
  const idOf = (arg: unknown) => commandItemId(arg, vscode.window.activeTextEditor?.document.uri, ["kraft-artifact"]) ?? preview.id();
  const gateOf = (id: string) => store.item(id)?.pending_gate ?? undefined;
  const warn = (msg: string) => void vscode.window.showWarningMessage(msg);

  // Resolves once the preview is up; only then is the render remembered as read.
  const show = async (id: string, gate: string | undefined, a: Artifact) => {
    const path = `/${id}/${artifactBasename(id, a.title)}`;
    artifacts.set(path, docText(a.content, a.truncated));
    const uri = vscode.Uri.from({ scheme: "kraft-artifact", path });
    sources.set(id, uri);
    changed.fire(uri); // a preview already open on this path re-renders
    await vscode.workspace.openTextDocument(uri);
    preview.opening(id); // before the tab appears, so its tab change finds the id
    await vscode.commands.executeCommand("markdown.showPreview", uri);
    if (gate) shown.record(id, gate, a.digest); // no gate to bind it to: shown, not remembered
  };

  /** Whether the document is now showing; a failure has already been reported. */
  const openArtifact = async (id?: string, gate?: string): Promise<boolean> => {
    if (!id) return false;
    gate ??= gateOf(id);
    try {
      const a = await api.getArtifact(id);
      if (!a) return void vscode.window.showInformationMessage(`${id}'s gate has no document to show.`), false;
      await show(id, gate, a);
      return true;
    } catch (e) {
      await reportError(e, store, id);
      return false;
    }
  };

  // Guarded here, not at registration: a gate notification's buttons call these directly.
  const approve = mutating("kraft.approve", refusal, warn, async (id?: string, gate?: string) => {
    if (!id) return;
    gate ??= gateOf(id);
    if (!gate) return void vscode.window.showInformationMessage(`${id} is not waiting at a gate.`);
    const g = gate;
    // Read once the approve is about to go: drafts written since the document opened go with it.
    const send = async (digest: string | undefined) => {
      const n = drafts(await cache.load(id)).length;
      if (!n) return api.approve(id, g, digest).then(() => true);
      const ok = await vscode.window.showWarningMessage(
        `Approve ${g} and send ${n} draft comment${n === 1 ? "" : "s"} with it? No agent acts on comments sent with an approval.`,
        { modal: true },
        "Approve and Send",
      );
      if (ok !== "Approve and Send") return false;
      // The review route stamps the drafts as part of this approval.
      await api.submitReview(id, g, { outcome: "approve", digest });
      await cache.load(id).catch(() => {});
      return true;
    };
    try {
      // Refused here, before the read-first fetch, while a must-fix thread is open.
      const outcome = await approveFlow({
        threads: () => cache.load(id),
        refuse: (r) => showRefusal(id, r, reveal),
        readOrSend: () => approveOrRead(api, shown, id, g, (a) => show(id, g, a), undefined, undefined, send),
      });
      if (outcome === "read-first") void vscode.window.showInformationMessage(`Read ${g} first: the document is open.`);
      if (outcome === "approved") await store.refresh(id);
    } catch (e) {
      // A failed re-open has already said why; one toast is enough.
      if (isStaleRevision(e) && !(await openArtifact(id, g))) return;
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

  const scoped = (ids: string[] | "all") =>
    announceable(ids === "all" ? store.items() : ids.map((id) => store.item(id)).filter((i) => i !== undefined), include);
  const toast = (found: [WorkItem, Announcement][]) => {
    for (const [item, a] of found) {
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
  store.onChange((ids) => toast(announcer.considerAll(scoped(ids))));
  store.onChange(() => shown.reconcile((id) => { const i = store.item(id); return i ? (i.pending_gate ?? null) : undefined; }));
  // A scope that became ready announces what already waits in it, once.
  // Items out of scope are not considered, so a stop that changed while out
  // of scope reads as new.
  scope.onChange(() => toast(announcer.rescope(scoped("all"), scope.showAll)));

  context.subscriptions.push(
    // From the palette, away from a gate document: ask which gate.
    vscode.commands.registerCommand("kraft.openArtifact", async (arg?: unknown) =>
      openArtifact(idOf(arg) ?? (await pickItem("openArtifact", onBoard(store.items(), include)))?.id)),
    vscode.commands.registerCommand("kraft.approve", (arg?: unknown, gate?: string) => approve(idOf(arg), gate)),
    vscode.commands.registerCommand("kraft.reject", (arg?: unknown, gate?: string, note?: string) => reject(idOf(arg), gate, note)),
    // The preview's markdown as text, for search, copy, or a line to quote.
    vscode.commands.registerCommand("kraft.openArtifactSource", async (arg?: unknown) => {
      const id = idOf(arg);
      const uri = id ? sources.get(id) : undefined;
      if (!uri) return void vscode.window.showInformationMessage("Open a gate document first: its source is the one it showed.");
      await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(uri), { preview: false });
    }),
  );
  return { announcer, shown, show: (id: string, gate: string, a: Artifact) => show(id, gate, a), openArtifact };
}
