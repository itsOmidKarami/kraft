import { describe, expect, it } from "vitest";
import type { DisplayStatus, StopKind, WorkItemStop } from "../../types";
import { archivable, budgetRaise, headerState } from "./status";

type Scope = WorkItemStop["scope"];
/** A budget stop is on the item's own cap unless a row says otherwise: the one `/budget/raise` takes. */
const at = (display_status: DisplayStatus, kind?: StopKind, scope: Scope = "work_item") =>
  headerState({ display_status, stop: kind ? { kind, node: "n", resume_at: null, reason: null, ...(kind === "budget" && { scope }) } : null });
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
    ["needs_you", "budget", "NEEDS YOU", "warn", "retry", FULL, "daily"],
    ["escalated", undefined, "ESCALATED", "warn", "pause", FULL],
    ["failed", "failed", "FAILED", "bad", "retry", FULL],
    ["paused", undefined, "PAUSED", "muted", "resume", FULL],
    ["done", undefined, "DONE", "ok", "archive", []],
    ["cancelled", undefined, "CANCELLED", "muted", "archive", ["archive"]],
    ["archived", undefined, "ARCHIVED", "muted", "restore", []],
  ] as const)("%s (%s) → %s, %s, %s", (status, kind, badge, tone, main, panel, scope?: Scope) => {
    expect(at(status, kind, scope)).toEqual({ badge, tone, main, panel });
  });

  it("Archive in the panel is live only for done and cancelled", () => {
    expect((["done", "cancelled"] as const).every(archivable)).toBe(true);
    expect((["running", "paused", "failed", "needs_you", "archived"] as const).some(archivable)).toBe(false);
  });
});

describe("budgetRaise: only a budget stop the server would raise offers a raise", () => {
  const stop = (kind: StopKind, over: Partial<WorkItemStop> = {}): WorkItemStop => ({ kind, node: "n", resume_at: null, reason: null, ...over });
  const POLICY_USD = { limit: { path: "", key: "budget_usd", value: 0.01, maximum: null } } as const;
  it.each<[string, WorkItemStop, "limit" | "item" | null]>([
    ["an item-wide policy budget_usd, by its limit", stop("budget", { ...POLICY_USD, scope: "usd" }), "limit"],
    ["the item's own cap", stop("budget", { scope: "work_item" }), "item"],
    // The item at its own cap too: /budget/raise still refuses, the node's cap stopped it.
    ["a node's budget_usd, the item also at its own cap", stop("budget", { scope: "usd" }), null],
    ["the daily cap", stop("budget", { scope: "daily" }), null],
    ["a token cap", stop("budget", { scope: "tokens" }), null],
    ["a stop event that names no cap", stop("budget"), null],
    ["a cap stop that names a limit", stop("cap", { limit: { path: "", key: "time_cap_minutes", value: 60, maximum: null } }), null],
  ])("%s", (_name, s, want) => {
    // Every item here has spent its own cap: the spend says nothing about which cap stopped it.
    const item = { stop: s, budget_cap: { cap_usd: 10, source: "item" as const, spent_usd: 10 } };
    expect(budgetRaise(item)).toBe(want);
  });
});
