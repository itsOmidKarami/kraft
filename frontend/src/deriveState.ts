import type { WorkItem } from "./types";

/**
 * The design vocabulary's status (handoff README "State the client needs" /
 * common spec "Status mapping"), derived once here and reused everywhere —
 * do not re-derive per component.
 *
 * `escalating` / `escalated` / `archived` are in the full vocabulary but not
 * representable yet: nothing in `WorkItem` distinguishes an escalation stop
 * from any other `needs_human` stop (that needs escalation sessions,
 * `hook_point === "escalation"`, not yet in the store), and no
 * `WorkItemStatus` value means archived. Both land with the UI v2 items that
 * add them (handoff README "Suggested order" steps 2 and 3).
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
  | "waiting";

export interface DerivedState {
  state: ItemDisplayState;
  needsYou: boolean;
}

/** The Prototype's `NEEDS` group (common spec): gate, capped, question,
 *  budget, escalated, paused. `escalated` is omitted — see the module doc. */
const NEEDS_YOU: ReadonlySet<ItemDisplayState> = new Set([
  "gate",
  "capped",
  "question",
  "budget",
  "paused",
]);

/** A created-but-never-resumed item: `paused` with no current node
 *  (api.py:527-529). Distinct from a mid-chain pause, which groups with
 *  "paused"/needs-you instead. */
function notStarted(item: WorkItem): boolean {
  return item.status === "paused" && item.current_node_id === null;
}

export function deriveState(item: WorkItem): DerivedState {
  let state: ItemDisplayState;
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
    if (item.pending_gate) state = "gate";
    else if (item.cappedOut) state = "capped";
    else if (item.budget) state = "budget";
    else if (item.needs_context_question) state = "question";
    // A needs_human stop always carries one of the four reasons above once
    // the server has fully reported it (store.ts:184-243 always sets exactly
    // one); this is a safety net for a stop this switch does not name yet,
    // not a real case today.
    else state = "gate";
  } else {
    // `active`, and anything this switch does not name yet.
    state = "running";
  }
  return { state, needsYou: NEEDS_YOU.has(state) };
}
