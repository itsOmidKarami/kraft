import type { ComponentProps } from "react";
import type { ChainNode } from "../../types";
import type { GateView } from "../graph/GateView";
import type { Sel } from "../graph/usePaneSelection";
import { nameOf } from "../board/rowText";
import { rejectTarget } from "./graph";
import { AUTO_REVIEW, sessionLook, sessionsOf } from "./nodeGraph";
import { byWord, gateRoundList, gateState, isPastRound, isReviewRound, newestOutcome, noteDoc, outcomeWord, ownOutcome, reviewDoc, roundShown, shownNote, threadCountLine, threadWhere } from "./gateRounds";
import { roundReviewUrl } from "../review/url";
import { LABEL_WORD } from "../review/sharedWords";
import { ENDED, gateMessage, materialized, nodeAt } from "./chainValues";
import { cursorOf } from "./cursor";
import { taskName } from "./paths";
import { OUTCOME } from "./reviewOutcome";
import type { ItemDetail } from "./useItem";

type Props = ComponentProps<typeof GateView>;

/** What clicking a gate's reviewer selects: its `auto_review` task when the frozen chain has one, else the gate's first listed task. */
export function reviewerSel(item: ItemDetail, gate: ChainNode): Sel {
  const frozen = materialized(item);
  const t = gate.tasks[0];
  if (frozen && nodeAt(frozen, gate.id)?.auto_review) return { kind: "task", node: gate.id, step: AUTO_REVIEW, task: AUTO_REVIEW };
  return t ? { kind: "task", node: gate.id, step: t.split(".")[1], task: taskName(t) } : { kind: "node", node: gate.id };
}

/** A gate's node view for W3's GateView (Decisions §6 Gates): its auto_review
 *  task (when it declares one) with its run state and verdict, the diamond,
 *  the document it decides on, and the reject branch. `picked` is the round
 *  the view shows (undefined: the newest): the gate is drawn as that round
 *  left it, with its note, and nothing of now (the document) on a past one. */
export function gateView(item: ItemDetail, gate: ChainNode, now: number, sel: Sel, on: { doc: () => void; reject: (to: string) => void; /** Open the round's whole note; the preview has no button without it. */ read?: (note: ReturnType<typeof noteDoc>, button: HTMLElement) => void; /** Open the round's whole review summary the same way. */ readReview?: (doc: ReturnType<typeof reviewDoc>, button: HTMLElement) => void }, picked?: number): Pick<Props, "gate" | "reviewer" | "message" | "doc" | "reject" | "youSub" | "rounds" | "said" | "review"> {
  const pending = item.pending_gate === gate.id;
  const round = roundShown(item, gate.id, picked);
  const past = isPastRound(item, gate.id, picked);
  const standing = gateState(item, gate.id);
  // The API's chain lists no task on a gate: its reviewer and message are in the frozen chain.
  const frozen = materialized(item);
  const own = frozen ? nodeAt(frozen, gate.id) : undefined;
  const path = own?.auto_review ? `${gate.id}.auto_review` : gate.tasks[0];
  const nodes = item.chain_definition.nodes;
  const ahead = !pending && !round && !ENDED.has(item.display_status ?? "") && (cursorOf(nodes, item.current_node_id)?.index ?? -1) < nodes.findIndex((n) => n.id === gate.id);
  const last = path ? sessionsOf(item, path).at(-1) : undefined;
  const outcome = round?.reviewer;
  const sentTo = round?.outcome === "rejected" ? round.sent_to : null;
  const to = sentTo ?? rejectTarget(nodes, gate.id);
  const who = round && byWord(round.by);
  // Ended only: the pending round holds threads too (the ones being made now), and has no review to show yet.
  const reviewed = !!round?.ended_at && isReviewRound(round);
  const summary = round?.review?.summary ?? "";
  return {
    gate: { id: gate.id, state: past ? PAST_STATE[round!.outcome] : standing.state, sel: sel.kind === "node" && sel.node === gate.id },
    reviewer: path
      ? { id: taskName(path), state: sessionLook(last, now).state, running: sessionLook(last, now).running, sel: sel.kind === "task" && sel.task === taskName(path), mode: own?.auto_review_mode ?? "decides", ...(outcome ? { chip: OUTCOME[outcome.kind].word, chipTone: OUTCOME[outcome.kind].tone } : ahead ? { chip: "runs first when reached" } : {}) }
      : undefined,
    message: gateMessage(frozen, gate.id),
    doc: pending && !past && item.gate_artifact ? { label: item.gate_artifact.split("/").pop()!, onClick: on.doc } : undefined,
    reject: to ? { id: to, label: nameOf(nodes, to), onClick: () => on.reject(to), ...(sentTo && { taken: true }), ...(round?.outcome === "fixed" && { fixed: true }) } : undefined,
    youSub: past ? `${outcomeWord(round!)}${who ? ` · ${who}` : ""}` : pending ? "waiting" : round?.outcome === "skipped" ? "skipped" : standing.state === "done" ? (who ?? undefined) : newestOutcome(item, gate.id),
    rounds: gateRoundList(item, gate.id, picked),
    review: reviewed
      ? {
          outcome: ownOutcome(round),
          summary,
          count: threadCountLine(round),
          threads: round.first_threads.map((t) => ({ id: t.id, where: threadWhere(t), label: t.label && LABEL_WORD[t.label], body: t.body })),
          href: roundReviewUrl(item.id, gate.id, round.n),
          onRead: summary && on.readReview ? (button: HTMLElement) => on.readReview!(reviewDoc(gate.id, round), button) : undefined,
        }
      : undefined,
    // Beside the review block, not in place of it: a person's note is what the next agent was told. Only the thread bounce's is left out, which is those threads as text.
    said: round && shownNote(round) && round.ended_at ? { note: shownNote(round)!, at: round.ended_at, n: round.n, onRead: on.read && ((button: HTMLElement) => on.read!(noteDoc(gate.id, round), button)) } : undefined,
  };
}

/** A past round's diamond: what became of it. */
const PAST_STATE = { approved: "done", fixed: "done", skipped: "done", rejected: "failed", closed: "failed", pending: "current" } as const;
