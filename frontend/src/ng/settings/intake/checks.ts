import type { Check } from "./types";

/** What a skip reason says, with the count and the priority ceiling (`GET /intake/checks`). */
const REASON: Record<string, (n: number, ceiling: number) => string> = {
  above_priority_ceiling: (n, c) => `${n} above P${c}`,
  already_item: (n) => `${n} already ${n === 1 ? "an item" : "items"}`,
  epic: (n) => `${n} ${n === 1 ? "epic" : "epics"}`,
  max_concurrent: (n) => `${n} left: max at a time reached`,
  daily_budget: (n) => `${n} left: daily budget spent`,
  invalid_config: (n) => `${n} left: invalid config`,
};

/** "4 ready · started kraft-d71a · 2 above P2 · 1 already an item". */
export function checkText(c: Check, ceiling: number): string {
  const counts = new Map<string, number>();
  for (const s of c.skipped) counts.set(s.reason, (counts.get(s.reason) ?? 0) + 1);
  const skipped = [...counts].map(([reason, n]) => (REASON[reason] ?? ((k: number) => `${k} ${reason.replaceAll("_", " ")}`))(n, ceiling));
  return [`${c.ready} ready`, c.started.length ? `started ${c.started.join(", ")}` : "none started", ...skipped].join(" · ");
}

/** When the poller checks next: it sleeps `interval_s` after each check, so the last one plus the interval
 *  (ST-3). Null when that can't be told: no check yet, or long past due, since a restart or a publish starts
 *  the poller's clock afresh. */
export function nextCheck(last: string | undefined, interval_s: number, now: number): string | null {
  const at = last ? Date.parse(last) : NaN;
  if (Number.isNaN(at)) return null;
  const ms = at + interval_s * 1000 - now;
  if (ms <= -60_000) return null;
  if (ms <= 60_000) return ms <= 0 ? "next check due now" : "next check in under a minute";
  const min = Math.ceil(ms / 60_000);
  const h = Math.round(min / 60);
  if (min < 60) return `next check in ${min} min`;
  if (h < 24) return `next check in ${h} h`;
  const d = Math.round(min / 1440);
  return `next check in ${d} day${d === 1 ? "" : "s"}`;
}

export const hhmm = (at: string) => {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
};
