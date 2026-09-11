import type { KraftEvent, WorkItem, WorkerSession } from "./types";

/**
 * The design vocabulary's status (handoff README "State the client needs" /
 * common spec "Status mapping"), derived once here and reused everywhere —
 * do not re-derive per component.
 */
export type ItemDisplayState =
  | "gate"
  | "capped"
  | "question"
  | "budget"
  | "not_started"
  | "done"
  | "abandoned"
  | "paused"
  | "running"
  | "rate_limited"
  | "waiting"
  | "escalating"
  | "escalated"
  | "archived";

export interface DerivedState {
  state: ItemDisplayState;
  needsYou: boolean;
}

/** The Prototype's `NEEDS` group (common spec): gate, capped, question,
 *  budget, escalated, paused. `escalating` (nobody is needed while the turn
 *  runs) and `archived` (README: "not needs-you") are deliberately omitted. */
const NEEDS_YOU: ReadonlySet<ItemDisplayState> = new Set([
  "gate",
  "capped",
  "question",
  "budget",
  "paused",
  "escalated",
]);

/** A created-but-never-resumed item: `paused` with no current node
 *  (api.py:527-529). Distinct from a mid-chain pause, which groups with
 *  "paused"/needs-you instead. */
function notStarted(item: WorkItem): boolean {
  return item.status === "paused" && item.current_node_id === null;
}

export function deriveState(
  item: WorkItem,
  sessions: WorkerSession[] = [],
  events: KraftEvent[] = [],
): DerivedState {
  let state: ItemDisplayState;
  if (item.archived_at) {
    return { state: "archived", needsYou: false };
  }
  if (item.status === "completed") {
    state = "done";
  } else if (item.status === "abandoned") {
    state = "abandoned";
  } else if (item.status === "rate_limited") {
    state = "rate_limited";
  } else if (item.status === "waiting") {
    state = "waiting";
  } else if (notStarted(item)) {
    state = "not_started";
  } else if (item.status === "paused") {
    state = "paused";
  } else if (item.status === "needs_human") {
    // Escalation sessions scoped to the *current* needs_human episode only —
    // a turn from an earlier, already-resolved stop on the same node must
    // not resurrect `escalated` after a later, unrelated stop. Same idiom
    // `board._stop_reason` uses server-side: bounded by whichever of
    // `work_item_needs_human` / `gate_requested` is newest, since a gate
    // stop supersedes an escalation episode without emitting its own
    // `work_item_needs_human` event.
    const episodeStart = [...events]
      .reverse()
      .find((e) => e.type === "work_item_needs_human" || e.type === "gate_requested")
      ?.created_at;
    const turns = sessions
      .filter(
        (s) => s.hook_point === "escalation" && (!episodeStart || s.created_at >= episodeStart),
      )
      .sort((a, b) => a.created_at.localeCompare(b.created_at));
    const latestTurn = turns.at(-1);
    if (latestTurn && (latestTurn.status === "pending" || latestTurn.status === "running")) {
      state = "escalating";
    } else if (latestTurn) {
      state = "escalated";
    } else if (item.pending_gate) state = "gate";
    else if (item.cappedOut) state = "capped";
    else if (item.budget) state = "budget";
    else if (item.needs_context_question) state = "question";
    // A needs_human stop with none of the four reasons above is a task
    // failure, no_progress, config error, or worktree-refresh failure (most
    // `mark_needs_human` callers) -- not a gate. `capped` is the state whose
    // action bar puts Retry back in reach even with `cappedOut` unset (the
    // pre-06 ActionBar routed these "stranded" items to CappedCard).
    else state = "capped";
  } else {
    // `active`, and anything this switch does not name yet.
    state = "running";
  }
  return { state, needsYou: NEEDS_YOU.has(state) };
}
