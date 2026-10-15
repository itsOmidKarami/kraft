import { groupOf, STATUS_GROUPS, type GroupKey } from "../../../frontend/src/ng/board/model";
import { nodeWords, reasonTail } from "../../../frontend/src/ng/board/rowText";
import type { WorkItem, WorkItemDetail } from "./api";
import { linkTokens } from "./links";
import { primaryAction, primaryToken } from "./primary";
import { spendLine, timeLine } from "./spend";

/** The board reads the server's `display_status` and `stop`, as the web board
 *  does (`groupOf`, `reasonTail`); nothing here derives state from `status`. */

/** What a row is, for its icon: the `display_status`, with a
 *  `needs_you` split by the stop kinds that look different and a paused item
 *  that never started apart from one paused mid-chain. */
export type ItemState =
  | "gate" | "question" | "budget" | "cap" | "needs_you" | "failed" | "paused" | "not_started"
  | "running" | "waiting" | "queued" | "blocked" | "escalated" | "done" | "cancelled" | "archived" | "unknown";

/** Glyphs differ wherever colour differs (failed `error`, capped `stop-circle`),
 *  so no state is told by colour alone. Running is static: a spinner on every
 *  running row would compete with the rows that wait on you. */
const ICONS: Record<ItemState, string> = {
  gate: "pass",
  question: "question",
  budget: "credit-card",
  cap: "stop-circle",
  needs_you: "warning",
  failed: "error",
  paused: "debug-pause",
  not_started: "debug-pause",
  running: "play-circle",
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

/** The items the board shows: in the window's scope and not archived. */
export const onBoard = (items: WorkItem[], include: Include) => items.filter((i) => shown(i) && include(i));

export function groupItems(items: WorkItem[], include: Include): { id: GroupKey; label: string; items: WorkItem[] }[] {
  const listed = onBoard(items, include);
  return STATUS_GROUPS.map((g) => ({
    id: g.key,
    label: g.label,
    items: listed.filter((i) => groupOf(i) === g.key),
  })).filter((g) => g.items.length > 0);
}

/** The group a shown item sits in, as `groupItems` places it; undefined when
 *  the board does not show it (archived, or outside the window's scope). */
export function groupHolding(items: WorkItem[], include: Include, id: string) {
  return groupItems(items, include).find((g) => g.items.some((i) => i.id === id));
}

/** The board's top level with the Archived group last: only under groups,
 *  so a board with nothing on it keeps its welcome text. */
export const withArchived = <G>(groups: G[]): (G | { kind: "archived" })[] =>
  groups.length ? [...groups, { kind: "archived" }] : [];

/** The Archived group's rows: the last list read of archived items, less any
 *  the store now holds un-archived (a restore lands there before the re-read),
 *  and the store's own copy of any it holds archived, which is the newer. */
export const archivedChildren = (cached: WorkItem[], store: { item(id: string): WorkItem | undefined }): WorkItem[] =>
  cached.flatMap((i) => {
    const held = store.item(i.id);
    return !held ? [i] : held.archived_at ? [held] : [];
  });

/** The board's welcome text: `here` says this repo has no Kraft work and
 *  offers Show all repos; `connect` says this repo is not connected at all
 *  (`unconnected`) and offers to connect it. Never before the scope is ready
 *  (git is async), never while disconnected (the Start row says that), and
 *  not when every repo is already shown. */
export type Welcome = "none" | "here" | "connect";

export function welcomeState(connected: boolean, ready: boolean, showAll: boolean, visible: number, unconnected = false): Welcome {
  if (!(connected && ready && !showAll && visible === 0)) return "none";
  return unconnected ? "connect" : "here";
}

const primaryTokens = (item: WorkItem) => {
  const p = primaryAction(item);
  return p ? [primaryToken(p)] : [];
};

/** The row's `contextValue`, which its menus match: `gate` while a gate is
 *  pending, then the server's `actions`, the doors it would admit now, then
 *  `drafts` while it has draft review comments (so Submit and Discard apply),
 *  then what it links to (`linkTokens`), then its inline button's token
 *  (`primaryToken`) when it has one, then `worktree` while its worktree is
 *  on disk. A server that sends no `actions` gets no action entries, only the
 *  ones every row has. */
export const contextValueOf = (item: WorkItem, { drafts = false }: { drafts?: boolean } = {}): string =>
  `item:${[
    ...(item.pending_gate ? ["gate"] : []),
    ...(item.actions ?? []),
    ...(drafts ? ["drafts"] : []),
    ...linkTokens(item),
    ...primaryTokens(item),
    ...(item.worktree_exists ? ["worktree"] : []),
  ].join(",")}`;

/** Whether a cached detail may fill the hover as is: not while the item runs,
 *  since its running time grows and the detail is re-read only on events. */
export const hoverFromCache = (item: WorkItem) => stateOf(item) !== "running";

/** Where Open Worktree goes: the detail's own path, only while it exists. */
export function worktreeTarget(detail: WorkItemDetail | undefined): string | undefined {
  return detail?.worktree_exists === true && detail.worktree_path ? detail.worktree_path : undefined;
}

export const needsYou = (item: WorkItem) => shown(item) && groupOf(item) === "needs";

export const needsYouCount = (items: WorkItem[], include: Include) => items.filter((i) => needsYou(i) && include(i)).length;

/** The items a toast may be about: one window per repo announces a gate. */
export const announceable = (items: WorkItem[], include: Include) => items.filter(include);

export const iconOf = (item: WorkItem) => ICONS[stateOf(item)];

/** The theme colour of a row's icon: a ThemeColor id. Waiting on you reads
 *  yellow, a fault red; everything else keeps the default. */
const COLORS: Partial<Record<ItemState, string>> = {
  gate: "list.warningForeground",
  question: "list.warningForeground",
  budget: "list.warningForeground",
  needs_you: "list.warningForeground",
  cap: "list.errorForeground",
  failed: "list.errorForeground",
};

export const colorOf = (item: WorkItem): string | undefined => COLORS[stateOf(item)];

/** Whether a stop is a fault, not a request: failed, capped, over budget or
 *  stuck on a conflict. A gate or a question is the item asking for you. */
export function isFault(item: Pick<WorkItem, "display_status" | "stop">): boolean {
  if (item.display_status === "failed") return true;
  return item.display_status === "needs_you" && item.stop?.kind !== "gate" && item.stop?.kind !== "question";
}

/** The row's description: why it is where it is, or its status when it has no
 *  reason. The id lives in the hover and in a quick pick's detail. */
export function describe(item: WorkItem): string {
  return reasonTail(item) || nodeWords(item.display_status ?? item.status);
}

/** What a screen reader says for a row: the title, its state, then the reason. */
export function itemLabel(item: WorkItem): string {
  const state = nodeWords(item.display_status ?? item.status);
  const reason = reasonTail(item);
  return [item.title, state, reason && reason !== state ? reason : ""].filter(Boolean).join(", ");
}

/** What a screen reader says for a group row: "Needs you, 3 items". */
export const groupLabel = (label: string, count: number) => `${label}, ${count} ${count === 1 ? "item" : "items"}`;

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/** Milliseconds until the text of a countdown row ("retry in 5m", `until`)
 *  next changes, or undefined when it never will (no clock, or expired).
 *  `until` floors to the minute below an hour, the hour below a day: the text
 *  turns over when the time left drops under a multiple of that unit, and
 *  "in 1m" holds to expiry. */
export function nextTick(items: Pick<WorkItem, "display_status" | "stop" | "retry_at">[], now: number): number | undefined {
  let next: number | undefined;
  for (const i of items) {
    const at = i.display_status === "waiting" ? (i.stop?.resume_at ?? i.retry_at) : undefined;
    const left = at ? Date.parse(at) - now : NaN;
    if (!(left > 0)) continue;
    const unit = left < 2 * MIN ? 0 : left < HOUR ? MIN : left < DAY ? HOUR : DAY;
    // Past the boundary by a millisecond, so the redraw reads the new text.
    const wait = unit ? left - Math.floor(left / unit) * unit + 1 : left;
    next = Math.min(next ?? Infinity, wait);
  }
  return next;
}

/** What a message calls an item: its title, or its id before the board has read it. */
export const nameOf = (store: { item(id: string): WorkItem | undefined }, id: string): string => store.item(id)?.title ?? id;

/** A question's text without the worker's `needs_context:` marker. */
export const questionText = (reason: string) => reason.replace(/^needs_context:\s*/, "");

/** The hover: the stop's whole reason when there is one (the row keeps only its first sentence),
 *  then, with the item's detail, what it has spent and how long it has run against their caps. */
export function tooltipOf(item: WorkItem, detail?: Pick<WorkItemDetail, "budget_cap" | "running_time">): string {
  const reason = item.stop?.reason;
  const first = `${item.id} — ${
    reason
      ? item.stop?.kind === "question" ? questionText(reason) : reason
      : reasonTail(item) || item.display_status || item.status}`;
  if (!detail) return first;
  return [first, spendLine(detail.budget_cap), timeLine(detail.running_time)].filter((l) => l !== null).join("\n");
}

/** The Resume/Retry input box's prompt: a question stop shows the question, so the steer answers it. */
export function steerPrompt(item: WorkItem): string {
  const reason = item.stop?.kind === "question" ? item.stop.reason : null;
  return reason ? `${item.title} asks: ${questionText(reason)} Your answer steers it (optional)` : `Steer for ${item.title} (optional)`;
}
