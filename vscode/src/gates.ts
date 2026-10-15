import * as vscode from "vscode";
import { gateWords } from "../../frontend/src/ng/board/rowText";
import { drafts } from "../../frontend/src/ng/review/finish";
import { reportError } from "./actions";
import type { Api, WorkItem } from "./core/api";
import { Announcer, toastActions, toastLevel, type Announcement } from "./core/announcer";
import { announceable, onBoard, type Include } from "./core/board";
import { approvedMessage, approveOrRead, consequenceQuestion, gateConsequence, isStaleRevision } from "./core/approve";
import { approveFlow } from "./core/mustFix";
import { showRefusal } from "./review/refusal";
import type { Artifact } from "./core/api";
import { ShownRenders } from "./core/shown";
import { mutating } from "./core/guard";
import type { WorkspaceScope } from "./core/repoScope";
import { artifactBasename, artifactUri, docText } from "./core/docs";
import { gateCall } from "./core/preview";
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
  const latest = new Map<string, string>(); // item id → the basename its latest render used
  const shown = new ShownRenders(); // what each item's tab last showed, so Approve binds to it
  const changed = new vscode.EventEmitter<vscode.Uri>();

  context.subscriptions.push(
    changed,
    vscode.workspace.registerTextDocumentContentProvider("kraft-artifact", {
      onDidChange: changed.event,
      provideTextDocumentContent: (uri) => artifacts.get(uri.path) ?? "",
    }),
  );

  const direct = (arg: unknown) => commandItemId(arg, vscode.window.activeTextEditor?.document.uri, ["kraft-artifact"]);
  // A preview's title bar passes a webview-panel uri: its item is the one that preview shows.
  const idOf = (arg: unknown) => direct(arg) ?? preview.target(arg)?.id;
  const gateOf = (id: string) => store.item(id)?.pending_gate ?? undefined;
  const warn = (msg: string) => void vscode.window.showWarningMessage(msg);

  // Resolves once the preview is up; only then is the render remembered as read.
  const show = async (id: string, gate: string | undefined, a: Artifact) => {
    const { scheme, path } = artifactUri(id, a.title);
    const basename = artifactBasename(id, a.title);
    artifacts.set(path, docText(a.content, a.truncated));
    const uri = vscode.Uri.from({ scheme, path });
    changed.fire(uri); // a preview already open on this path re-renders
    const doc = await vscode.workspace.openTextDocument(uri);
    latest.set(id, basename);
    preview.opening(id, basename); // before the tab appears, so its tab change finds it
    // Locked, so it never follows the active editor onto someone else's file.
    if ((await vscode.commands.getCommands(true)).includes("markdown.showPreview"))
      await vscode.commands.executeCommand("markdown.showPreview", uri, undefined, { locked: true });
    else await vscode.window.showTextDocument(doc, { preview: false }); // the Markdown extension is disabled
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

  // Clicked on a preview of an older render than the latest: open the latest instead of acting.
  const readFirst = async (id: string, gate: string) => {
    if (await openArtifact(id, gate)) void vscode.window.showInformationMessage(`Read ${gate} first: the document is open.`);
  };

  // Guarded here, not at registration: a gate notification's buttons call these directly.
  // `stale`: clicked on an older render's preview; it reads first, as an unread gate does.
  const approve = mutating("kraft.approve", refusal, warn, async (id?: string, gate?: string, stale = false) => {
    if (!id) return;
    gate ??= gateOf(id);
    if (!gate) return void vscode.window.showInformationMessage(`${id} is not waiting at a gate.`);
    const g = gate;
    if (stale) return readFirst(id, g);
    // Read once the approve is about to go: drafts written since the document opened go with it.
    const send = async (digest: string | undefined) => {
      const n = drafts(await cache.load(id)).length;
      const item = store.item(id);
      const what = item && gateConsequence(item, g);
      if (!n && what) {
        const ok = await vscode.window.showWarningMessage(consequenceQuestion(item.title, what), { modal: true }, "Approve");
        if (ok !== "Approve") return false;
      }
      if (!n) return api.approve(id, g, digest).then(() => true);
      const ok = await vscode.window.showWarningMessage(
        `Approve ${gateWords(g)} on ${item?.title ?? id} and send ${n} draft comment${n === 1 ? "" : "s"} with it?${what ? ` This also ${what === "merge" ? "merges it into the target branch" : "pushes it"}.` : ""} No agent acts on comments sent with an approval.`,
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
      // The approve went through: a failed re-read is not a failed approve.
      if (outcome === "approved") {
        const title = store.item(id)?.title ?? id;
        await store.refresh(id).catch(() => {});
        void vscode.window.showInformationMessage(approvedMessage(title, g, store.item(id)));
      }
    } catch (e) {
      // A failed re-open has already said why; one toast is enough.
      if (isStaleRevision(e) && !(await openArtifact(id, g))) return;
      await reportError(e, store, id);
    }
  });

  const reject = mutating("kraft.reject", refusal, warn, async (id?: string, gate?: string, note?: string, stale = false) => {
    if (!id) return;
    gate ??= gateOf(id);
    if (!gate) return void vscode.window.showInformationMessage(`${id} is not waiting at a gate.`);
    if (stale) return readFirst(id, gate);
    note ??= await vscode.window.showInputBox({
      prompt: `Why reject ${store.item(id)?.title ?? id} at ${gateWords(gate)}?`,
      validateInput: (v) => (v.trim() ? undefined : "A reject needs a reason: it goes to the agent."),
    });
    if (!note?.trim()) return;
    try {
      await api.reject(id, gate, note.trim());
      shown.forget(id);
      await store.refresh(id).catch(() => {});
    } catch (e) {
      await reportError(e, store, id);
    }
  });

  const scoped = (ids: string[] | "all") =>
    announceable(ids === "all" ? store.items() : ids.map((id) => store.item(id)).filter((i) => i !== undefined), include);
  const toast = (found: [WorkItem, Announcement][]) => {
    for (const [item, a] of found) {
      if (a.kind === "gate") {
        const buttons = refusal() === null ? ["Open", "Reject…"] : ["Open"];
        void vscode.window.showInformationMessage(`${item.title}: ${a.text}`, ...buttons).then((pick) => {
          if (pick === "Open") void openArtifact(item.id, a.gate);
          if (pick === "Reject…") void reject(item.id, a.gate);
        });
      } else {
        const buttons = toastActions(item, refusal() !== null);
        const text = `${item.title} — ${a.text}`;
        const labels = buttons.map((b) => b.label);
        void (toastLevel(item) === "warning" ? vscode.window.showWarningMessage(text, ...labels) : vscode.window.showInformationMessage(text, ...labels)).then((pick) => {
          const run = buttons.find((b) => b.label === pick);
          if (run) void vscode.commands.executeCommand(run.command, { item });
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
    vscode.commands.registerCommand("kraft.approve", (arg?: unknown, gate?: unknown) => {
      const c = gateCall(direct(arg), preview.target(arg), latest, gate);
      return approve(c.id, c.gate, c.stale);
    }),
    vscode.commands.registerCommand("kraft.reject", (arg?: unknown, gate?: unknown, note?: unknown) => {
      const c = gateCall(direct(arg), preview.target(arg), latest, gate, note);
      return reject(c.id, c.gate, c.note, c.stale);
    }),
  );
  return { announcer, shown, show: (id: string, gate: string, a: Artifact) => show(id, gate, a), openArtifact };
}
