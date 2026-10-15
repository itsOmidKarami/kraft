import { humanSize } from "../../sizes";
import type { StorageUsage } from "../../types";

/** The server's eight categories, in the order Storage lists them, on both layouts. */
export const CATEGORIES: [key: string, label: string][] = [
  ["worktrees", "Worktrees"],
  ["sandboxes", "Sandbox stores and homes"],
  ["documents", "Item documents and session output"],
  ["logs", "Logs"],
  ["results", "Results"],
  ["databases", "Databases and backups"],
  ["attachments", "Attachments"],
  ["other", "Everything else"],
];

export const HOUSEKEEPING = "/settings/policy/housekeeping";

/** The line under the bar: what the state means for starts. Empty when all is well. */
export function storageNote(data: StorageUsage | null, measuring: boolean): string {
  const used = data?.used_bytes ?? 0;
  const limit = data?.limit_bytes ?? null;
  if (measuring && data) return "Measuring…";
  if (data?.state === "held") return "Over the limit: starts are held until you make room.";
  if (data?.state === "over_quota" && limit != null && used > limit) return "Over the limit in items' documents, which archiving keeps: starts still go ahead. Abandoning an item removes its documents.";
  if (data?.state === "over_quota") return "Over the quota: Kraft is warning; starts still go ahead.";
  return "";
}

export const NOTHING_TO_CLEAN = "Nothing to clean up: no finished item holds a worktree. A running item is paused or abandoned from its own page.";

/** The clean-up action has one name, here and on the phone; an item it can take is "can be cleaned up". */
export const CLEAN_SELECTED = "Clean up selected";
export const CLEANABLE = "can be cleaned up";
export const cleanAll = (count: number, bytes: number) => `Clean up all (${count}, ${humanSize(bytes)})`;

/** The key figure, said once above the bar: what the worktrees use, against the limit when there is one. */
export const usageFigure = (used: number, limit: number | null) => (limit != null ? `${humanSize(used)} used of the ${humanSize(limit)} limit` : `${humanSize(used)} used by worktrees`);
