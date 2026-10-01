import type { DisplayStatus, WorkItem } from "../../types";

/** What the header shows for an item. Read from the server's `display_status`
 *  and `stop` only (R16, R27, R28): never derived from sessions or events. */
export type Main = "pause" | "resume" | "raise" | "retry" | "archive" | "restore";
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

/** GAP §1.4a, Decisions §1 and §14. `raise` is the capped Resume: it opens the
 *  chain's Config at the limit that stopped the item. */
export function headerState(item: Pick<WorkItem, "display_status" | "stop">): HeaderState {
  const status = item.display_status ?? "running";
  const kind = item.stop?.kind;
  const main: Main =
    status === "paused" ? "resume"
    : status === "needs_you" && (kind === "cap" || kind === "budget") ? "raise"
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
  raise: "Resume",
  retry: "Retry",
  archive: "Archive",
  restore: "Restore",
};

/** Archive in the panel is live only once there is nothing left to stop. */
export const archivable = (status: DisplayStatus | undefined) => status === "done" || status === "cancelled";
