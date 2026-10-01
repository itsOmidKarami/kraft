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

export const hhmm = (at: string) => {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
};
