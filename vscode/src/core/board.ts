import { groupOf, STATUS_GROUPS, type GroupKey } from "../../../frontend/src/ng/board/model";
import { reasonTail } from "../../../frontend/src/ng/board/rowText";
import type { WorkItem } from "./api";

/** The board reads the server's `display_status` and `stop`, as the web board
 *  does (`groupOf`, `reasonTail`); nothing here derives state from `status`. */

export type Action = "pause" | "resume" | "retry" | "cancel" | "skip" | "escalate" | "archive";

/** What a row is, for its icon and its actions: the `display_status`, with a
 *  `needs_you` split by the stop kinds that look different and a paused item
 *  that never started apart from one paused mid-chain. */
export type ItemState =
  | "gate" | "question" | "budget" | "cap" | "needs_you" | "failed" | "paused" | "not_started"
  | "running" | "waiting" | "queued" | "blocked" | "escalated" | "done" | "cancelled" | "archived" | "unknown";

const ACTIONS: Record<ItemState, Action[]> = {
  running: ["pause", "cancel"],
  waiting: ["pause", "cancel"],
  queued: ["pause", "cancel"],
  blocked: ["pause", "cancel"],
  escalated: ["cancel"],
  paused: ["resume", "cancel"],
  not_started: ["resume", "cancel"],
  gate: ["skip", "cancel"],
  question: ["retry", "skip", "escalate", "cancel"],
  cap: ["retry", "skip", "escalate", "cancel"],
  needs_you: ["retry", "skip", "escalate", "cancel"],
  failed: ["retry", "skip", "escalate", "cancel"],
  // The server refuses escalate on every budget stop.
  budget: ["retry", "skip", "cancel"],
  done: ["archive"],
  cancelled: ["archive"],
  archived: [],
  unknown: [],
};

const ICONS: Record<ItemState, string> = {
  gate: "pass",
  question: "question",
  budget: "credit-card",
  cap: "error",
  needs_you: "warning",
  failed: "error",
  paused: "debug-pause",
  not_started: "debug-pause",
  running: "sync~spin",
  waiting: "watch",
  queued: "watch",
  blocked: "watch",
  escalated: "comment-discussion",
  done: "check",
  cancelled: "circle-slash",
  archived: "archive",
  unknown: "circle-filled",
};

const SPLIT_STOPS = new Set(["gate", "question", "budget", "cap"]);
const AS_IS = new Set<string>(["failed", "running", "waiting", "queued", "blocked", "escalated", "done", "cancelled", "archived"]);

export function stateOf(item: WorkItem): ItemState {
  const d = item.display_status;
  if (item.archived_at) return "archived";
  if (d === "needs_you") return item.stop && SPLIT_STOPS.has(item.stop.kind) ? (item.stop.kind as ItemState) : "needs_you";
  if (d === "paused") return item.current_node_id ? "paused" : "not_started";
  return d && AS_IS.has(d) ? (d as ItemState) : "unknown";
}

const shown = (i: WorkItem) => !i.archived_at;

/** Whether an item is in the window's scope (`WorkspaceScope.includes`):
 *  the board, the badge and the toasts all ask the same question. */
export type Include = (item: WorkItem) => boolean;

export function groupItems(items: WorkItem[], include: Include): { id: GroupKey; label: string; items: WorkItem[] }[] {
  const listed = items.filter((i) => shown(i) && include(i));
  return STATUS_GROUPS.map((g) => ({
    id: g.key,
    label: g.label,
    items: listed.filter((i) => groupOf(i) === g.key),
  })).filter((g) => g.items.length > 0);
}

/** The board's welcome text: `here` says this repo has no Kraft work and
 *  offers Show all repos. Never before the scope is ready (git is async),
 *  never while disconnected (the Start row says that), and not when every
 *  repo is already shown. */
export type Welcome = "none" | "here";

export function welcomeState(connected: boolean, ready: boolean, showAll: boolean, visible: number): Welcome {
  return connected && ready && !showAll && visible === 0 ? "here" : "none";
}

export const actionsFor = (item: WorkItem): Action[] => ACTIONS[stateOf(item)];

export const needsYou = (item: WorkItem) => shown(item) && groupOf(item) === "needs";

export const needsYouCount = (items: WorkItem[], include: Include) => items.filter((i) => needsYou(i) && include(i)).length;

/** The items a toast may be about: one window per repo announces a gate. */
export const announceable = (items: WorkItem[], include: Include) => items.filter(include);

export const iconOf = (item: WorkItem) => ICONS[stateOf(item)];

/** The row's description: the id, then why it is where it is. */
export function describe(item: WorkItem): string {
  const tail = reasonTail(item);
  return tail ? `${item.id} · ${tail}` : item.id;
}

/** A question's text without the worker's `needs_context:` marker. */
export const questionText = (reason: string) => reason.replace(/^needs_context:\s*/, "");

/** The hover: the stop's whole reason when there is one (the row keeps only its first sentence). */
export function tooltipOf(item: WorkItem): string {
  const reason = item.stop?.reason;
  if (reason) return item.stop?.kind === "question" ? questionText(reason) : reason;
  return `${item.id} — ${reasonTail(item) || item.display_status || item.status}`;
}

/** The Resume/Retry input box's prompt: a question stop shows the question, so the steer answers it. */
export function steerPrompt(item: WorkItem): string {
  const reason = item.stop?.kind === "question" ? item.stop.reason : null;
  return reason ? `${item.id} asks: ${questionText(reason)} Your answer steers it (optional)` : `Steer for ${item.id} (optional)`;
}
