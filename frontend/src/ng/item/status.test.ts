import { describe, expect, it } from "vitest";
import type { BudgetCap, DisplayStatus, StopKind, WorkItemStop } from "../../types";
import { archivable, budgetRaise, headerState } from "./status";

/** The item's own $10 cap, spent: the budget stop `/budget/raise` raises. */
const OWN_CAP: BudgetCap = { cap_usd: 10, source: "item", spent_usd: 10 };
const at = (display_status: DisplayStatus, kind?: StopKind, budget_cap: BudgetCap = OWN_CAP) =>
  headerState({ display_status, stop: kind ? { kind, node: "n", resume_at: null, reason: null } : null, budget_cap });
const FULL = ["escalate", "complete", "archive", "cancel"];

describe("headerState: every display status, from the server's fields only", () => {
  it.each([
    ["running", undefined, "RUNNING", "neutral", "pause", FULL],
    ["waiting", "rate_limit", "WAITING", "info", "pause", FULL],
    ["needs_you", "gate", "NEEDS YOU", "warn", "pause", FULL],
    ["needs_you", "question", "NEEDS YOU", "warn", "pause", FULL],
    ["needs_you", "conflict", "NEEDS YOU", "warn", "pause", FULL],
    ["needs_you", "mr_closed", "NEEDS YOU", "warn", "pause", FULL],
    ["needs_you", "cap", "NEEDS YOU", "warn", "raise", FULL],
    ["needs_you", "budget", "NEEDS YOU", "warn", "raise", FULL],
    ["needs_you", "budget", "NEEDS YOU", "warn", "retry", FULL, { cap_usd: 10, source: "policy", spent_usd: 2 }],
    ["escalated", undefined, "ESCALATED", "warn", "pause", FULL],
    ["failed", "failed", "FAILED", "bad", "retry", FULL],
    ["paused", undefined, "PAUSED", "muted", "resume", FULL],
    ["done", undefined, "DONE", "ok", "archive", []],
    ["cancelled", undefined, "CANCELLED", "muted", "archive", ["archive"]],
    ["archived", undefined, "ARCHIVED", "muted", "restore", []],
  ] as const)("%s (%s) → %s, %s, %s", (status, kind, badge, tone, main, panel, cap?: BudgetCap) => {
    expect(at(status, kind, cap)).toEqual({ badge, tone, main, panel });
  });

  it("Archive in the panel is live only for done and cancelled", () => {
    expect((["done", "cancelled"] as const).every(archivable)).toBe(true);
    expect((["running", "paused", "failed", "needs_you", "archived"] as const).some(archivable)).toBe(false);
  });
});

describe("budgetRaise: only a budget stop the server would raise offers a raise", () => {
  const stop = (kind: StopKind, over: Partial<WorkItemStop> = {}): WorkItemStop => ({ kind, node: "n", resume_at: null, reason: null, ...over });
  const POLICY_USD = { limit: { path: "", key: "budget_usd", value: 0.01, maximum: null } } as const;
  it.each<[string, WorkItemStop, BudgetCap | undefined, "limit" | "item" | null]>([
    ["an item-wide policy budget_usd, by its limit", stop("budget", POLICY_USD), { cap_usd: 10, source: "policy", spent_usd: 0.04 }, "limit"],
    ["the item's own cap, its spend at the cap", stop("budget"), OWN_CAP, "item"],
    ["the item's own cap, past it", stop("budget"), { cap_usd: 10, source: "item", spent_usd: 10.4 }, "item"],
    ["the daily cap, the item under its own", stop("budget"), { cap_usd: 10, source: "policy", spent_usd: 2, daily: { spent_usd: 50, cap_usd: 50 } }, null],
    ["a token or node cap, the item with no dollar cap", stop("budget"), { cap_usd: null, source: "item", spent_usd: 30 }, null],
    ["no budget_cap on the item", stop("budget"), undefined, null],
    ["a cap stop that names a limit", stop("cap", { limit: { path: "", key: "time_cap_minutes", value: 60, maximum: null } }), OWN_CAP, null],
  ])("%s", (_name, s, budget_cap, want) => expect(budgetRaise({ stop: s, budget_cap })).toBe(want));
});
