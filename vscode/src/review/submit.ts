import * as vscode from "vscode";
import { drafts, outcomes } from "../../../frontend/src/ng/review/finish";
import { reportError } from "../actions";
import { ApiError, type Api, type Artifact, type FixTarget, type ReviewResult } from "../core/api";
import { approveOrRead, isStaleRevision } from "../core/approve";
import { mutating } from "../core/guard";
import { commandItemId } from "../core/review";
import { nodeToSend, restartChoices } from "../core/restart";
import type { ShownRenders } from "../core/shown";
import type { Store } from "../core/store";
import { canSend, OUTCOME_LABEL, outcomeChoices, submitMessage, type Outcome } from "../core/submit";
import type { ThreadCache } from "../core/threadCache";
import { discardPlan } from "../core/threads";

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
) {
  const info = (msg: string) => void vscode.window.showInformationMessage(msg);
  const warn = (msg: string) => void vscode.window.showWarningMessage(msg);

  /** True once a review was sent. With `choice`, no picker or input box opens. */
  const submit = async (id: string, choice?: { outcome: Outcome; summary?: string; node?: string }): Promise<boolean> => {
    const no = refusal();
    if (no) return warn(no), false;
    let threads;
    try {
      await store.refresh(id);
      threads = await cache.load(id);
    } catch (e) {
      await reportError(e, store, id);
      return false;
    }
    const detail = store.detail(id);
    const gate = detail?.pending_gate ?? null;
    // Where Request changes would restart: the detail's at a gate, the server's reckoning without one.
    let fix: FixTarget | null = detail?.fix_target ?? null;
    let unknown: string | undefined;
    if (detail && !gate) {
      try {
        fix = await api.fixTarget(id);
      } catch (e) {
        unknown = e instanceof ApiError ? e.detail : String(e);
      }
    }
    const choices = outcomeChoices(detail, fix, unknown);
    if ("refused" in choices) return warn(choices.refused), false;
    if (choice && !choices.includes(choice.outcome)) return warn(`${OUTCOME_LABEL[choice.outcome]} is not available on ${id} now.`), false;
    let { outcome, summary } = choice ?? {};
    if (!outcome) {
      const rows = outcomes(detail!, gate, threads, fix);
      const picked = await vscode.window.showQuickPick(
        choices.map((o) => ({ label: OUTCOME_LABEL[o], detail: rows.find((r) => r.key === o)?.parts.map((p) => p.t).join(""), o })),
        { placeHolder: `Submit your review of ${id}` },
      );
      if (!picked) return false;
      outcome = picked.o;
      summary = await vscode.window.showInputBox({ prompt: "Overall note for the agent (optional)" });
      if (summary === undefined) return false;
    }
    summary = summary?.trim() || undefined;
    const n = drafts(threads).length;
    if (!canSend(outcome, n, summary)) return info("No review comments to send."), false;
    let node = choice?.node;
    let fixNode = fix?.node;
    if (outcome === "request_changes" && !choice) {
      const at = gate ? { gate } : { current: detail!.current_node_id ?? "" };
      const restart = restartChoices(detail!.chain_definition?.nodes ?? [], at, fix!);
      if ("refused" in restart) return warn(restart.refused), false;
      const picked = await vscode.window.showQuickPick(restart, { title: `Request changes on ${id}: restart at` });
      if (!picked) return false;
      node = nodeToSend(picked);
      fixNode = picked.node;
    } else if (node) fixNode = node;
    try {
      if (outcome === "approve") {
        const g = gate!;
        let sent: ReviewResult | undefined;
        const r = await approveOrRead(api, shown, id, g, (a) => show(id, g, a), undefined, undefined, async (digest) => {
          sent = await api.submitReview(id, g, { outcome: "approve", ...(summary && { summary }), digest });
          return true;
        });
        if (r === "read-first") return info(`Read ${g} first: the document is open.`), false;
        if (r === "cancelled" || !sent) return false;
        info(submitMessage("approve", sent, { id, n, gate: g, fixNode: null, paused: false }));
      } else {
        const r = await api.submitReview(id, gate, { outcome, ...(summary && { summary }), ...(outcome === "request_changes" && node && { node }) });
        // The gate's document will be revised: the render shown so far no longer binds an approve.
        if (gate && outcome === "request_changes") shown.forget(id);
        info(submitMessage(outcome, r, { id, n, gate, fixNode, paused: detail!.status === "paused" }));
      }
    } catch (e) {
      // A failed re-open has already said why; one toast is enough.
      if (isStaleRevision(e) && gate && !(await openArtifact(id, gate))) return false;
      await reportError(e, store, id);
      return false;
    }
    await cache.load(id).catch(() => {});
    await store.refresh(id).catch(() => {});
    return true;
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

  const idOf = (arg: unknown) => commandItemId(arg, vscode.window.activeTextEditor?.document.uri, ["kraft-wt", "kraft-git", "kraft-artifact", "kraft-threads"]);
  context.subscriptions.push(
    vscode.commands.registerCommand("kraft.review.submit", mutating("kraft.review.submit", refusal, warn, (arg?: unknown) => {
      const id = idOf(arg);
      if (id) return submit(id);
    })),
    vscode.commands.registerCommand("kraft.review.discard", mutating("kraft.review.discard", refusal, warn, (arg?: unknown) => {
      const id = idOf(arg);
      if (id) return discard(id);
    })),
  );
  return { submit, discard };
}
