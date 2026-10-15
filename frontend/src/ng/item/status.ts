import type { DisplayStatus, ItemAction, WorkItem } from "../../types";
import { elapsed, usd } from "../../format";

/** What the header shows for an item. Read from the server's `display_status`
 *  and `stop` only (R16, R27, R28): never derived from sessions or events. */
export type Main = "pause" | "resume" | "start" | "raise" | "retry" | "archive" | "restore" | "gate" | "answer" | "conflicts" | "reopen";
export type Tone = "neutral" | "info" | "warn" | "bad" | "ok" | "muted";
export type PanelItem = "escalate" | "complete" | "archive" | "cancel";

export interface HeaderState {
  badge: string;
  tone: Tone;
  /** Null when the server admits none of the status's ways on (`admits`):
   *  no main button, and the panel's doors go to the ⋮ instead. */
  main: Main | null;
  /** The hover panel's items; empty means no panel and no ▾ toggle. */
  panel: PanelItem[];
}

const FULL: PanelItem[] = ["escalate", "complete", "archive", "cancel"];

const TONE: Record<DisplayStatus, Tone> = {
  running: "neutral",
  waiting: "info",
  queued: "info",
  blocked: "info",
  needs_you: "warn",
  escalated: "warn",
  failed: "bad",
  done: "ok",
  paused: "muted",
  cancelled: "muted",
  archived: "muted",
};

/** What `admits` reads: the server's `actions` when it sends them. */
type Doors = { actions?: readonly ItemAction[] | null };

/** Whether a surface offers the door `verb`: the server's `actions` when it
 *  sends them (`vocab.work_item.open_doors`, every route's own guard), else
 *  `rule`, the UI's own reading of the item, kept for a server older than
 *  them. Which door is the main one, its label and its tone stay the UI's. */
export function admits(item: Doors, verb: ItemAction, rule: boolean): boolean {
  return item.actions ? item.actions.includes(verb) : rule;
}

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

/** Whether a budget stop's Raise is offered: the item can raise the cap
 *  (`budgetRaise`, which also picks the editor) and the server admits the
 *  door that editor goes through: `raise_budget` for the item's own cap
 *  (`/budget/raise`), `retry` for a policy limit (PATCHed, then retried).
 *  Otherwise the way on is Retry. */
export function offersRaise(item: Pick<WorkItem, "stop"> & Doors): boolean {
  const how = budgetRaise(item);
  return how === "item" ? admits(item, "raise_budget", true) : how === "limit" ? admits(item, "retry", true) : false;
}

/** What a budget stop says when the item could raise its cap but the server takes no raise now (`offersRaise`). */
export const RAISE_REFUSED = "Kraft takes no raise for it right now.";

/** What a budget stop the item cannot raise says instead of offering a raise. */
export const NOT_RAISABLE = "The item can't raise this cap: the policy or the chain sets it. Retry once it is raised there, or after local midnight for the daily cap.";

/** A blocked item with nothing left to wait for (after Unblock, until the scheduler's next pass), in both layouts' words. */
export const RELEASED = { where: "nothing left to wait for", text: "Kraft queues it within seconds." };

/** A paused item with no current node was filed and never started (the
 *  server's row says so, as `board/model`'s `groupOf` reads it): it is "not
 *  started", with Start, wherever it is shown, never "paused" with Resume.
 *  How it is shown; `chainValues`' `notStarted` is the wider "no node has run
 *  yet" that decides whether its config can still be edited. */
export const neverStarted = (item: Pick<WorkItem, "display_status" | "current_node_id">) => item.display_status === "paused" && !item.current_node_id;

/** What `escalatable` reads: the detail's sessions and pending gate are optional, a board row has neither. */
type EscalateFields = Pick<WorkItem, "display_status" | "current_node_id"> & Partial<Pick<WorkItem, "pending_gate" | "stop">> & Doors & { worker_sessions?: { status: string; hook_point: string }[] };

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
  admits(item, "retry", (item.display_status === "failed" || item.display_status === "needs_you" || item.display_status === "escalated") && !reviewingGate(item));

/** Whether `/pause` would take this item: it claims only a running or waiting
 *  one (`active`, `waiting`, `rate_limited`) and answers every stopped item 409
 *  "work item is needs_human, not running" (R11b-01): a gate, a question, a stuck
 *  loop or a live escalation is never paused. */
export const pausable = (item: Pick<WorkItem, "display_status"> & Doors) => admits(item, "pause", item.display_status === "running" || item.display_status === "waiting");

/** Whether `/skip` would take this item: it refuses a rate-limited one (Kraft
 *  relaunches it at its `retry_at`; pause it first, which `/pause` takes, then
 *  Skip or Resume), and one whose escalation turn is running (R11b-01). */
export const skippable = (item: Pick<WorkItem, "status" | "stop"> & Partial<Pick<WorkItem, "display_status">> & Doors) => admits(item, "skip", skipsNow(item));

/** `skippable`'s own reading of the item, without the server's `actions`: their `skip` is the node's (whether the node
 *  the item stands on may be skipped), and a task's or a step's skip is decided by that task or step instead. */
export const skipsNow = (item: Pick<WorkItem, "status" | "stop"> & Partial<Pick<WorkItem, "display_status">>) =>
  item.status !== "rate_limited" && item.stop?.kind !== "rate_limit" && item.display_status !== "escalated";

/** Whether `/escalate` would take this item: a stopped one (failed or needs
 *  you) or a paused one that has started, and not while an escalation turn
 *  already runs. A running or waiting item answers 409. Nor does it take a
 *  pending gate with a live session: an agent's review of the gate, or a
 *  human's escalation turn, which the server shows as needs-you because the
 *  gate wins (`board.display_status`), and either is "already running"
 *  (#504 review P2-1). Nor a spend cap's stop: the escalation's agent would
 *  hit the same cap, so the server refuses it until the cap is raised (R12E-05). */
export const escalatable = (item: EscalateFields) =>
  admits(item, "escalate",
    (item.display_status === "failed" || item.display_status === "needs_you" || (item.display_status === "paused" && !!item.current_node_id))
    && !(item.pending_gate && item.worker_sessions?.some(live))
    && !(item.display_status !== "paused" && item.stop?.kind === "budget"));

/** The ways on from a `needs_you` stop, by its kind, first choice first: the
 *  same table as the phone's bottom bar (`phone/item/model`'s `pairOf`), so the
 *  two layouts offer the same door (R11b-01). Never Pause: `/pause` refuses
 *  every stopped item. A budget stop's Raise, and a closed merge request's
 *  Reopen when none is on file, fall back to Retry. */
function needsYouMain(item: Pick<WorkItem, "stop"> & Doors): Main[] {
  switch (item.stop?.kind) {
    case "gate": return ["gate"];
    case "question": return ["answer"];
    case "budget": return budgetRaise(item) ? ["raise", "retry"] : ["retry"];
    case "cap": return ["raise"];
    case "conflict": return ["conflicts"];
    case "mr_closed": return ["reopen", "retry"];
    // stuck, and any stop a later server adds: /retry claims every needs_human item.
    default: return ["retry"];
  }
}

/** The door each main button goes through, or null for one that opens a view
 *  with its own decisions (a gate, the conflicts): always offered. Raise on a
 *  budget stop is `offersRaise`'s; a cap's limit editor patches, then retries. */
const MAIN_DOOR: Record<Main, ItemAction | null> = {
  pause: "pause", resume: "resume", start: "resume", answer: "resume", raise: "retry", retry: "retry",
  archive: "archive", restore: "restore", reopen: "reopen_mr", gate: null, conflicts: null,
};

/** Whether the server admits `main`'s door; true for every door on a server that sends no `actions`. */
function mainAdmitted(item: Pick<WorkItem, "stop"> & Doors, main: Main): boolean {
  if (main === "raise" && item.stop?.kind === "budget") return offersRaise(item);
  const door = MAIN_DOOR[main];
  return door === null || admits(item, door, true);
}

/** GAP §1.4a, Decisions §1 and §14. `raise` opens the budget or limit editor
 *  for the cap that stopped the item. A budget stop the item cannot raise
 *  (`budgetRaise`) has Retry instead. A never-started item (`neverStarted`) has
 *  Start, and nothing to escalate or mark complete. Pause only where `/pause`
 *  takes it (`pausable`); an escalated item has Retry, which outranks its turn. */
export function headerState(item: Pick<WorkItem, "display_status" | "stop" | "current_node_id"> & Omit<EscalateFields, "display_status" | "current_node_id">): HeaderState {
  const status = item.display_status ?? "running";
  const first = (ways: Main[]) => ways.find((m) => mainAdmitted(item, m)) ?? null;
  if (neverStarted(item)) return { badge: "NOT STARTED", tone: "muted", main: first(["start"]), panel: admits(item, "cancel", true) ? ["cancel"] : [] };
  const main = first(
    status === "paused" ? ["resume"]
    : status === "needs_you" ? needsYouMain(item)
    : status === "failed" || status === "escalated" ? ["retry"]
    : status === "done" || status === "cancelled" ? ["archive"]
    : status === "archived" ? ["restore"]
    : ["pause"],
  );
  const panel: PanelItem[] =
    status === "done" || status === "archived" ? []
    : status === "cancelled" ? ["archive"]
    : FULL.filter((p) =>
      p === "escalate" ? escalatable({ ...item, display_status: status })
      : p === "complete" || p === "cancel" ? admits(item, p, true)
      // Archive stays in the panel as a disabled hint until `archivable`.
      : true);
  return { badge: status.replace("_", " ").toUpperCase(), tone: TONE[status], main, panel };
}

/** Whether the item page's banner or state card already carries `main` as its own primary button,
 *  so the page's header leaves it out (one primary per state, next to the words that explain it).
 *  Retry counts only where a card has it: a failed or stuck stop, not a budget the item cannot
 *  raise (its banner has no button) or a closed MR's fallback. Start, Pause, Restore, an
 *  escalated item's Retry and a done item's Archive have no card, so the header keeps them. */
export function bodyOffers(item: Pick<WorkItem, "display_status" | "stop" | "needs_context_question">, main: Main | null): boolean {
  const status = item.display_status;
  switch (main) {
    case "gate": case "conflicts": case "raise": case "reopen": case "resume": return true;
    case "answer": return !!item.needs_context_question;
    case "archive": return status === "cancelled";
    case "retry": return !!item.stop && (status === "failed" || (status === "needs_you" && !["gate", "budget", "cap", "mr_closed", "conflict", "question"].includes(item.stop.kind)));
    default: return false;
  }
}

/** Whether a banner or card on the item page already offers the review (a gate's, a rebase's conflicts). */
export const reviewedBelow = (item: Pick<WorkItem, "display_status" | "stop">) =>
  item.display_status === "needs_you" && (item.stop?.kind === "gate" || item.stop?.kind === "conflict");

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

/** The header's ⋮ doors that act on the item: Duplicate once it has ended. Escalate… and
 *  Cancel… are the main button's menu's only, so the ⋮ never repeats them (WI-13). */
export function menuDoors(item: Pick<WorkItem, "display_status">): "duplicate"[] {
  return ["done", "cancelled", "archived"].includes(item.display_status ?? "") ? ["duplicate"] : [];
}

/** Archive in the panel is live only once there is nothing left to stop, and the server admits it. */
export const archivable = (item: Pick<WorkItem, "display_status"> & Doors) => admits(item, "archive", item.display_status === "done" || item.display_status === "cancelled");

/** "$2.41 of $5.00 · 1h 12m of 8h": the spend, and the running time beside it when the item has a time cap (WI-7). */
export function spentLine(item: Pick<WorkItem, "budget_cap" | "running_time">): string {
  const cap = item.budget_cap;
  const time = item.running_time;
  const money = cap ? `${usd(cap.spent_usd)}${cap.cap_usd != null ? ` of ${usd(cap.cap_usd)}` : ""}` : "";
  const clock = time?.cap_minutes != null ? `${elapsed(time.running_s * 1000)} of ${elapsed(time.cap_minutes * 60_000)}` : "";
  return [money, clock].filter(Boolean).join(" · ");
}
