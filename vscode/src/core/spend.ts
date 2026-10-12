import { elapsed, usd } from "../../../frontend/src/format";
import type { BudgetCap, BudgetToday, WorkItem } from "../../../frontend/src/types";

/** An item's running time against its cap, as the detail endpoint reports it. */
export type RunningTime = NonNullable<WorkItem["running_time"]>;

/** What an item has spent against its dollar cap; null when the payload has none. */
export function spendLine(b?: BudgetCap | null): string | null {
  if (!b) return null;
  return b.cap_usd == null ? `Spent ${usd(b.spent_usd)} (no cap)` : `Spent ${usd(b.spent_usd)} of ${usd(b.cap_usd)}`;
}

/** How long an item has run against its time cap; null when the payload has none. */
export function timeLine(rt?: RunningTime | null): string | null {
  if (!rt) return null;
  const ran = elapsed(rt.running_s * 1000);
  return rt.cap_minutes == null ? `Ran ${ran} (no cap)` : `Ran ${ran} of ${elapsed(rt.cap_minutes * 60_000)}`;
}

/** The instance's spend since local midnight against its daily cap (`GET /budget/today`). */
export function todayLine(t?: BudgetToday | null): string | null {
  if (!t) return null;
  return t.cap_usd == null ? `Today: ${usd(t.spent_usd)} (no daily cap)` : `Today: ${usd(t.spent_usd)} of ${usd(t.cap_usd)}`;
}
