import type { EventType } from "./types/vocab.generated";

/** Status-moving events after which display_status and stop are re-read.
 *  React-free: the VS Code extension imports it too. */
export const REREAD_EVENTS: ReadonlySet<EventType> = new Set<EventType>([
  "work_item_needs_human",
  "work_item_resumed",
  "work_item_retried",
  "pause_requested",
  "work_item_waiting",
  "work_item_rate_limited",
  "work_item_completed",
  "work_item_cancelled",
  "node_skipped",
  "work_item_queued",
  "work_item_dequeued",
  "work_item_abandoned",
  "gate_approved",
  "gate_rejected",
  "work_item_blocked_by_dependency",
  "paused_by_broken_base",
  "work_item_archived",
  "work_item_restored",
  // A one-off chain approved, or the chain an item runs swapped, moves `one_off` and the chain it shows.
  "one_off_chain_approved",
  "chain_template_changed",
  // store.mark_reentered flips waiting/rate_limited to active and clears retry_at with no event of its own; the reducer leaves status alone here.
  "node_started",
  // An escalation turn going live or ending flips escalated, which the reducer never patches.
  "escalation_message",
  "worker_session_paused",
  "session_unknown",
  "worker_session_exited",
  "work_item_blocked",
  "work_item_unblocked",
  // A must-fix thread resolved, reopened or sent with a review moves `must_fix_open`, and with it the approve door in `actions`.
  "thread_updated",
  "review_submitted",
]);
