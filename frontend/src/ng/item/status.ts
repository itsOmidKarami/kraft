import type { DisplayStatus, WorkItem } from "../../types";

/** What the header shows for an item. Read from the server's `display_status`
 *  and `stop` only (R16, R27, R28): never derived from sessions or events. */
export type Main = "pause" | "resume" | "start" | "raise" | "retry" | "archive" | "restore" | "gate" | "answer" | "conflicts" | "reopen";
export type Tone = "neutral" | "info" | "warn" | "bad" | "ok" | "muted";
export type PanelItem = "escalate" | "complete" | "archive" | "cancel";

export interface HeaderState {
  badge: string;
  tone: Tone;
  main: Main;
  /** The hover panel's items; empty means no panel and no ▾ toggle. */
  panel: PanelItem[];
}

const FULL: PanelItem[] = ["escalate", "complete", "archive", "cancel"];

const TONE: Record<DisplayStatus, Tone> = {
  running: "neutral",
  waiting: "info",
  needs_you: "warn",
  escalated: "warn",
  failed: "bad",
  done: "ok",
  paused: "muted",
  cancelled: "muted",
  archived: "muted",
};

/** How the cap behind a budget stop is raised, or null when the item cannot
 *  raise it, so neither layout offers a raise the server would refuse (409):
 *  - `limit`: the stop names an item-wide policy `budget_usd`, raised in the
 *    item's policy and retried (the stop's Raise cap);
 *  - `item`: the stop's `scope` is `work_item`, the item's own dollar cap,
 *    raised through `/budget/raise`, the one scope that route takes;
 *  - null: a daily or token cap, a node's, or spend no harness reported,
 *    which the policy or the chain raises, then Retry. */
export function budgetRaise(item: Pick<WorkItem, "stop">): "limit" | "item" | null {
  if (item.stop?.kind !== "budget") return null;
  if (item.stop.limit) return "limit";
  return item.stop.scope === "work_item" ? "item" : null;
}

/** What a budget stop the item cannot raise says instead of offering a raise. */
export const NOT_RAISABLE = "The item can't raise this cap: the policy or the chain sets it. Retry once it is raised there, or after local midnight for the daily cap.";

/** A paused item with no current node was filed and never started (the
 *  server's row says so, as `board/model`'s `groupOf` reads it): it is "not
 *  started", with Start, wherever it is shown, never "paused" with Resume.
 *  How it is shown; `chainValues`' `notStarted` is the wider "no node has run
 *  yet" that decides whether its config can still be edited. */
export const neverStarted = (item: Pick<WorkItem, "display_status" | "current_node_id">) => item.display_status === "paused" && !item.current_node_id;

/** What `escalatable` reads: the detail's sessions and pending gate are optional, a board row has neither. */
type EscalateFields = Pick<WorkItem, "display_status" | "current_node_id"> & Partial<Pick<WorkItem, "pending_gate">> & { worker_sessions?: { status: string; hook_point: string }[] };

const live = (s: { status: string }) => s.status === "running" || s.status === "pending";

/** A pending gate an agent is reviewing: its walk is live, and /retry, /skip and /escalate answer it
 *  "a walk is already running". A human's escalation turn is not a walk: a retry outranks it. */
const reviewingGate = (item: Omit<EscalateFields, "display_status" | "current_node_id">) =>
  !!item.pending_gate && !!item.worker_sessions?.some((s) => live(s) && s.hook_point !== "escalation" && !s.hook_point.endsWith(".escalation"));

/** Whether `/retry` would take this item. It claims only a stopped item
 *  (`needs_human`, which the server shows as failed, needs you or escalated)
 *  and answers 409 "work item is not stopped" to a paused, running or waiting
 *  one, so no surface offers Retry there (R10b-01): a paused item has Resume.
 *  Nor a gate an agent is reviewing (`reviewingGate`, #504 review P2-1). */
export const retryable = (item: Pick<WorkItem, "display_status"> & Omit<EscalateFields, "display_status" | "current_node_id">) =>
  (item.display_status === "failed" || item.display_status === "needs_you" || item.display_status === "escalated") && !reviewingGate(item);

/** Whether `/pause` would take this item: it claims only a running or waiting
 *  one (`active`, `waiting`, `rate_limited`) and answers every stopped item 409
 *  "work item is needs_human, not running" (R11b-01): a gate, a question, a stuck
 *  loop or a live escalation is never paused. */
export const pausable = (item: Pick<WorkItem, "display_status">) => item.display_status === "running" || item.display_status === "waiting";

/** Whether `/skip` would take this item: it refuses a rate-limited one (Kraft
 *  relaunches it at its `retry_at`; pause it first, which `/pause` takes, then
 *  Skip or Resume), and one whose escalation turn is running (R11b-01). */
export const skippable = (item: Pick<WorkItem, "status" | "stop"> & Partial<Pick<WorkItem, "display_status">>) =>
  item.status !== "rate_limited" && item.stop?.kind !== "rate_limit" && item.display_status !== "escalated";

/** Whether `/escalate` would take this item: a stopped one (failed or needs
 *  you) or a paused one that has started, and not while an escalation turn
 *  already runs. A running or waiting item answers 409. Nor does it take a
 *  pending gate with a live session: an agent's review of the gate, or a
 *  human's escalation turn, which the server shows as needs-you because the
 *  gate wins (`board.display_status`), and either is "already running"
 *  (#504 review P2-1). */
export const escalatable = (item: EscalateFields) =>
  (item.display_status === "failed" || item.display_status === "needs_you" || (item.display_status === "paused" && !!item.current_node_id))
  && !(item.pending_gate && item.worker_sessions?.some(live));

/** The way on from a `needs_you` stop, by its kind: the same table as the
 *  phone's bottom bar (`phone/item/model`'s `pairOf`), so the two layouts offer
 *  the same door (R11b-01). Never Pause: `/pause` refuses every stopped item. */
function needsYouMain(item: Pick<WorkItem, "stop">): Main {
  switch (item.stop?.kind) {
    case "gate": return "gate";
    case "question": return "answer";
    case "budget": return budgetRaise(item) ? "raise" : "retry";
    case "cap": return "raise";
    case "conflict": return "conflicts";
    case "mr_closed": return "reopen";
    // stuck, and any stop a later server adds: /retry claims every needs_human item.
    default: return "retry";
  }
}

/** GAP §1.4a, Decisions §1 and §14. `raise` opens the budget or limit editor
 *  for the cap that stopped the item. A budget stop the item cannot raise
 *  (`budgetRaise`) has Retry instead. A never-started item (`neverStarted`) has
 *  Start, and nothing to escalate or mark complete. Pause only where `/pause`
 *  takes it (`pausable`); an escalated item has Retry, which outranks its turn. */
export function headerState(item: Pick<WorkItem, "display_status" | "stop" | "current_node_id"> & Omit<EscalateFields, "display_status" | "current_node_id">): HeaderState {
  const status = item.display_status ?? "running";
  if (neverStarted(item)) return { badge: "NOT STARTED", tone: "muted", main: "start", panel: ["cancel"] };
  const main: Main =
    status === "paused" ? "resume"
    : status === "needs_you" ? needsYouMain(item)
    : status === "failed" || status === "escalated" ? "retry"
    : status === "done" || status === "cancelled" ? "archive"
    : status === "archived" ? "restore"
    : "pause";
  const panel: PanelItem[] =
    status === "done" || status === "archived" ? []
    : status === "cancelled" ? ["archive"]
    : FULL.filter((p) => p !== "escalate" || escalatable({ ...item, display_status: status }));
  return { badge: status.replace("_", " ").toUpperCase(), tone: TONE[status], main, panel };
}

export const MAIN_LABEL: Record<Main, string> = {
  pause: "Pause",
  resume: "Resume",
  start: "Start",
  raise: "Raise cap",
  retry: "Retry",
  archive: "Archive",
  restore: "Restore",
  gate: "Open gate",
  answer: "Answer",
  conflicts: "Review conflicts",
  reopen: "Reopen MR",
};

/** The header's ⋮ doors that act on the item: Duplicate once it has ended,
 *  else Escalate… where `/escalate` takes it (`escalatable`) and Cancel…. */
export function menuDoors(item: EscalateFields): ("duplicate" | "escalate" | "cancel")[] {
  if (["done", "cancelled", "archived"].includes(item.display_status ?? "")) return ["duplicate"];
  return [...(escalatable(item) ? ["escalate" as const] : []), "cancel"];
}

/** Archive in the panel is live only once there is nothing left to stop. */
export const archivable = (status: DisplayStatus | undefined) => status === "done" || status === "cancelled";
