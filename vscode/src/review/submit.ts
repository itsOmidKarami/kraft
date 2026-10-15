import * as vscode from "vscode";
import { drafts, outcomes } from "../../../frontend/src/ng/review/finish";
import { reportError } from "../actions";
import { ApiError, type Api, type Artifact, type FixTarget, type ReviewResult } from "../core/api";
import { approveOrRead, isStaleRevision } from "../core/approve";
import { mutating } from "../core/guard";
import { approveFlow } from "../core/mustFix";
import { commandItemId } from "../core/review";
import { nodeToSend, restartChoices } from "../core/restart";
import type { ShownRenders } from "../core/shown";
import type { Store } from "../core/store";
import { canSend, OUTCOME_LABEL, outcomeChoices, submitMessage, type Outcome } from "../core/submit";
import type { ThreadCache } from "../core/threadCache";
import { discardPlan, hasDrafts } from "../core/threads";
import { showRefusal } from "./refusal";

/** Why a Submit sent nothing: refused up front, a must-fix thread open, the document to read first, backed out, nothing to send, or an error. */
export type SubmitResult = { ok: true } | { ok: false; why: "refused" | "must-fix" | "read-first" | "cancelled" | "nothing" | "error" };
const not = (why: Extract<SubmitResult, { ok: false }>["why"]): SubmitResult => ({ ok: false, why });

/** Submit Review and Discard Review Comments: the item's drafts, wherever they were written, through the server. */
export function registerReviewSubmit(
  context: vscode.ExtensionContext,
  store: Store,
  api: Api,
  refusal: () => string | null,
  cache: ThreadCache,
  shown: ShownRenders,
  show: (id: string, gate: string, a: Artifact) => Promise<void>,
  openArtifact: (id: string, gate?: string) => Promise<boolean>,
  reveal: (id: string, tid: string) => Promise<boolean | undefined>,
  previewId: (arg: unknown) => string | undefined,
) {
  const info = (msg: string) => void vscode.window.showInformationMessage(msg);
  const warn = (msg: string) => void vscode.window.showWarningMessage(msg);

  /** Whether a review was sent, and why not. With `choice`, no picker or input box opens. */
  const submit = async (id: string, choice?: { outcome: Outcome; summary?: string; node?: string }): Promise<SubmitResult> => {
    const no = refusal();
    if (no) return warn(no), not("refused");
    let threads;
    try {
      await store.refresh(id);
      threads = await cache.load(id);
    } catch (e) {
      await reportError(e, store, id);
      return not("error");
    }
    const detail = store.detail(id);
    const gate = detail?.pending_gate ?? null;
    // Where Request changes would restart: the detail's at a gate, the server's reckoning without one.
    let fix: FixTarget | null = detail?.fix_target ?? null;
    let unknown: string | undefined;
    // An ended or unloaded item is refused before the slow gateless reckoning.
    const first = outcomeChoices(detail);
    if ("refused" in first) return warn(first.refused), not("refused");
    if (!gate) {
      try {
        fix = await api.fixTarget(id);
      } catch (e) {
        unknown = e instanceof ApiError ? e.detail : String(e);
      }
    }
    const choices = outcomeChoices(detail, fix, unknown);
    if ("refused" in choices) return warn(choices.refused), not("refused");
    if (choice && !choices.includes(choice.outcome)) return warn(`${OUTCOME_LABEL[choice.outcome]} is not available on ${id} now.`), not("refused");
    let { outcome, summary } = choice ?? {};
    if (!outcome) {
      const rows = outcomes(detail!, gate, threads, fix);
      const picked = await vscode.window.showQuickPick(
        choices.map((o) => ({ label: OUTCOME_LABEL[o], detail: rows.find((r) => r.key === o)?.parts.map((p) => p.t).join(""), o })),
        { placeHolder: `Submit your review of ${id}` },
      );
      if (!picked) return not("cancelled");
      outcome = picked.o;
    }
    let node = choice?.node;
    let fixNode = node ?? fix?.node;
    // Where it restarts before the note: backing out here loses nothing typed.
    if (outcome === "request_changes" && !choice) {
      const at = gate ? { gate } : { current: detail!.current_node_id ?? "" };
      const restart = restartChoices(detail!.chain_definition?.nodes ?? [], at, fix!);
      if ("refused" in restart) return warn(restart.refused), not("refused");
      const picked = await vscode.window.showQuickPick(restart, { title: `Request changes on ${id}: restart at` });
      if (!picked) return not("cancelled");
      node = nodeToSend(picked);
      fixNode = picked.node;
    }
    if (!choice) {
      summary = await vscode.window.showInputBox({ prompt: "Overall note for the agent (optional)" });
      if (summary === undefined) return not("cancelled");
    }
    summary = summary?.trim() || undefined;
    const n = drafts(threads).length;
    if (!canSend(outcome, n, summary)) return info("No review comments to send."), not("nothing");
    try {
      if (outcome === "approve") {
        const g = gate!;
        let sent: ReviewResult | undefined;
        const r = await approveFlow({
          // Again here: a thread may have been labelled must-fix while the pickers were open.
          threads: () => cache.load(id),
          refuse: (refusal) => showRefusal(id, refusal, reveal),
          readOrSend: () => approveOrRead(api, shown, id, g, (a) => show(id, g, a), undefined, undefined, async (digest) => {
            sent = await api.submitReview(id, g, { outcome: "approve", ...(summary && { summary }), digest });
            return true;
          }),
        });
        if (r === "refused") return not("must-fix");
        if (r === "read-first") return info(`Read ${g} first: the document is open.`), not("read-first");
        if (r === "cancelled" || !sent) return not("cancelled");
        info(submitMessage("approve", sent, { id, title: detail!.title, n, gate: g, fixNode: null, paused: false }));
      } else {
        const r = await api.submitReview(id, gate, { outcome, ...(summary && { summary }), ...(outcome === "request_changes" && node && { node }) });
        // The gate's document will be revised: the render shown so far no longer binds an approve.
        if (gate && outcome === "request_changes") shown.forget(id);
        info(submitMessage(outcome, r, { id, title: detail!.title, n, gate, fixNode, paused: detail!.status === "paused" }));
      }
    } catch (e) {
      // A failed re-open has already said why; one toast is enough.
      if (isStaleRevision(e) && gate && !(await openArtifact(id, gate))) return not("error");
      await reportError(e, store, id);
      return not("error");
    }
    await cache.load(id).catch(() => {});
    await store.refresh(id).catch(() => {});
    return { ok: true };
  };

  /** Deletes every draft on the item, one request each, stopping at the first refusal. */
  const discard = async (id: string, confirmed = false): Promise<boolean> => {
    const no = refusal();
    if (no) return warn(no), false;
    let plan;
    try {
      plan = discardPlan(await cache.load(id));
    } catch (e) {
      await reportError(e, store, id);
      return false;
    }
    const n = plan.count;
    if (!n) return info("No draft comments to discard."), false;
    if (!confirmed) {
      const ok = await vscode.window.showWarningMessage(
        `Discard ${n} draft comment${n === 1 ? "" : "s"}? They are deleted for the web UI and the CLI too.`, { modal: true }, "Discard");
      if (ok !== "Discard") return false;
    }
    let done = true;
    try {
      for (const tid of plan.threads) await api.deleteThread(tid);
      for (const cid of plan.comments) await api.deleteComment(cid);
    } catch (e) {
      done = false;
      await reportError(e, store, id);
    }
    await cache.load(id).catch(() => {});
    return done;
  };

  const idOf = (arg: unknown) => commandItemId(arg, vscode.window.activeTextEditor?.document.uri, ["kraft-wt", "kraft-git", "kraft-artifact", "kraft-threads"]) ?? previewId(arg);
  context.subscriptions.push(
    vscode.commands.registerCommand("kraft.review.submit", mutating("kraft.review.submit", refusal, warn, (arg?: unknown) => {
      // The clicked tab's item first: the title-bar key is the active editor's, which may be another group's.
      const id = idOf(arg);
      if (!id) return;
      const threads = cache.get(id);
      if (arg instanceof vscode.Uri && threads && !hasDrafts(threads)) return info(`${id} has no draft comments to submit.`);
      return submit(id);
    })),
    vscode.commands.registerCommand("kraft.review.discard", mutating("kraft.review.discard", refusal, warn, (arg?: unknown) => {
      const id = idOf(arg);
      if (id) return discard(id);
    })),
  );
  return { submit, discard };
}
