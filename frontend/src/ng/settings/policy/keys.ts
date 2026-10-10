import type { Unit } from "./types";

/** The `set_value` ops the Policy page sends: a scope, the dotted key in
 *  `policy.yaml`, and how its number reads and parses. One table, so a key the
 *  page can edit is a key a section owns (`sections.ts`). */
export interface KeySpec {
  scope: "limits" | "loops" | "housekeeping" | "findings" | "escalation" | "retries";
  key: string;
  unit: Unit;
  /** Zero is a legal value (a delay, an archive age). */
  zero?: boolean;
}

export const spec = (scope: KeySpec["scope"], key: string, unit: Unit, zero = false): KeySpec => ({ scope, key, unit, zero });

export const KEYS = {
  daily: spec("limits", "budget.daily_usd", "usd"),
  outer: spec("limits", "budget.work_item_usd", "usd"),
  attemptsDefault: spec("limits", "defaults.max_attempts", "count"),
  attemptsMax: spec("limits", "maxima.max_attempts", "count"),
  wallDefault: spec("limits", "defaults.timeout_minutes", "min"),
  wallMax: spec("limits", "maxima.timeout_minutes", "min"),
  loopAttempts: spec("loops", "default.attempts", "count"),
  loopWall: spec("loops", "default.wall_clock_s", "s-as-min"),
  stuckCap: spec("escalation", "auto_escalate_stuck_cap", "count"),
  stuckDelay: spec("escalation", "auto_escalate_delay_s", "s", true),
  reviewAttempts: spec("escalation", "auto_review_attempts", "count"),
  concurrent: spec("housekeeping", "max_concurrent", "count"),
  archive: spec("housekeeping", "archive.after_days", "days", true),
  storageLimit: spec("housekeeping", "storage.worktrees.limit", "size"),
  storageQuota: spec("housekeeping", "storage.worktrees.quota", "size"),
  storageCleanup: spec("housekeeping", "storage.worktrees.auto_cleanup.min_age", "age"),
  relaunch: spec("retries", "rate_limit_retries", "count"),
  forge: spec("retries", "forge_cli_timeout_s", "s"),
} as const;

export const SEVERITIES = ["critical", "important", "minor"] as const;
export const SEVERITY_KEY = { scope: "findings", key: "findings.loop_severities" } as const;
export const STUCK_KEY = { scope: "escalation", key: "auto_escalate_stuck" } as const;

/** A cap cell's key: `defaults.<level>.<cap>` or `maxima.<level>.<cap>`. */
export const capKey = (kind: "default" | "maximum", level: string, cap: string): KeySpec => ({
  scope: "limits",
  key: `${kind === "default" ? "defaults" : "maxima"}.${level}.${cap}`,
  unit: cap === "budget_usd" ? "usd" : cap === "token_budget" ? "tok" : "min",
});
