import type { Analytics } from "../../types";

export const WEEKS = 8;

export interface Week {
  /** The Monday, `YYYY-MM-DD`, as the server keys it. */
  start: string;
  /** `m/d`. */
  label: string;
  n: number;
  partial: boolean;
}

/** The last eight weeks, oldest first, a week with no merges as 0. The server
 *  keys each bucket by the Monday of the event's *UTC* date, so the columns are
 *  built in UTC too: local date parts read 0 for the newest weeks anywhere far
 *  enough ahead of UTC. */
export function weekBuckets(weekly: Analytics["weekly_merged"], now = new Date()): Week[] {
  const monday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7));
  const byWeek = new Map(weekly.map((w) => [w.week_start, w.n]));
  return Array.from({ length: WEEKS }, (_, i) => {
    const d = new Date(monday);
    d.setUTCDate(d.getUTCDate() - (WEEKS - 1 - i) * 7);
    const start = d.toISOString().slice(0, 10);
    return { start, label: `${d.getUTCMonth() + 1}/${d.getUTCDate()}`, n: byWeek.get(start) ?? 0, partial: i === WEEKS - 1 };
  });
}
