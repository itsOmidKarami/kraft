import type { DisplayStatus, WorkItem } from "../../types";

/** What the header shows for an item. Read from the server's `display_status`
 *  and `stop` only (R16, R27, R28): never derived from sessions or events. */
export type Main = "pause" | "resume" | "start" | "raise" | "retry" | "archive" | "restore";
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

/** GAP §1.4a, Decisions §1 and §14. `raise` is the capped Resume: it opens the
 *  chain's Config at the limit that stopped the item. A budget stop the item
 *  cannot raise (`budgetRaise`) has Retry instead. A never-started item
 *  (`neverStarted`) has Start, and nothing to escalate or mark complete. */
export function headerState(item: Pick<WorkItem, "display_status" | "stop" | "current_node_id">): HeaderState {
  const status = item.display_status ?? "running";
  if (neverStarted(item)) return { badge: "NOT STARTED", tone: "muted", main: "start", panel: ["cancel"] };
  const kind = item.stop?.kind;
  const main: Main =
    status === "paused" ? "resume"
    : status === "needs_you" && kind === "budget" ? (budgetRaise(item) ? "raise" : "retry")
    : status === "needs_you" && kind === "cap" ? "raise"
    : status === "failed" ? "retry"
    : status === "done" || status === "cancelled" ? "archive"
    : status === "archived" ? "restore"
    : "pause";
  const panel: PanelItem[] =
    status === "done" || status === "archived" ? []
    : status === "cancelled" ? ["archive"]
    : FULL;
  return { badge: status.replace("_", " ").toUpperCase(), tone: TONE[status], main, panel };
}

export const MAIN_LABEL: Record<Main, string> = {
  pause: "Pause",
  resume: "Resume",
  start: "Start",
  raise: "Resume",
  retry: "Retry",
  archive: "Archive",
  restore: "Restore",
};

/** Archive in the panel is live only once there is nothing left to stop. */
export const archivable = (status: DisplayStatus | undefined) => status === "done" || status === "cancelled";
