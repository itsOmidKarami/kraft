import { REREAD_EVENTS as WEB_REREADS } from "../../../frontend/src/rereadEvents";
import { EVENT_TYPES } from "../../../frontend/src/types/vocab.generated";

/** The web store's status-moving set: after one of these, display_status and stop are re-read. */
export const REREAD_EVENTS: ReadonlySet<string> = WEB_REREADS;

/** What else the extension shows that those events don't move. The web store
 *  patches or re-reads these by other paths; the extension only re-reads. */
const EXTRA: ReadonlySet<string> = new Set([
  // The findings roll-up and the pending gate.
  "gate_requested",
  // progress: "task N of M".
  "plan_progress",
  // The web store hydrates these only for an unknown item; a known one's chain may still change.
  "work_item_created",
  "chain_loaded",
  // step and the fallback tail (slice 01 put both on the detail), and the title.
  "worker_session_created",
  "worker_session_started",
  "launch_fallback",
  "work_item_title_edited",
  // mr_ref: a done row reads "merged !N".
  "mr_opened",
  "mr_closed",
  "mr_reopened",
]);

const KNOWN: ReadonlySet<string> = new Set<string>(EVENT_TYPES);

/** Whether an event can change what the extension shows for its item. When
 *  unsure, re-read: an item it doesn't hold, or an event type newer than this build. */
export function shouldReread(type: string, known: boolean): boolean {
  return !known || REREAD_EVENTS.has(type) || EXTRA.has(type) || !KNOWN.has(type);
}
