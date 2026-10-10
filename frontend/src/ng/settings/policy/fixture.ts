import type { DraftView, Problem, Result } from "../../templates/draft/types";
import type { PolicyResolved } from "./types";

const leaf = <T,>(value: T | null, source: string | null = value == null ? null : "policy") => ({ value, source });

const cap = (def: number | null, max: number | null, level: string, maxLevel: string | null = max == null ? null : level, below: unknown[] = []) => ({
  default: leaf(def, def == null ? null : level), maximum: leaf(max, maxLevel), below,
});

export const RESOLVED = {
  limits: {
    caps: {
      work_item: { time_cap_minutes: cap(480, 1440, "work_item"), total_time_cap_minutes: cap(null, null, "work_item"), budget_usd: cap(20, 25, "work_item"), token_budget: cap(null, 2000000, "work_item") },
      nodes: { time_cap_minutes: cap(180, 1440, "nodes", "work_item"), total_time_cap_minutes: cap(null, null, "nodes"), budget_usd: cap(null, 25, "nodes", "work_item"), token_budget: cap(null, 2000000, "nodes", "work_item") },
      steps: { time_cap_minutes: cap(120, 1440, "steps", "work_item"), total_time_cap_minutes: cap(null, null, "steps"), budget_usd: cap(null, 25, "steps", "work_item"), token_budget: cap(null, 2000000, "steps", "work_item") },
      tasks: { time_cap_minutes: cap(90, 240, "tasks", "tasks", [{ layer: "library", chain: "default", path: "implementation.main.implement", via: "library:tasks.implementer", value: 120, exceeds: false }]), total_time_cap_minutes: cap(null, 10080, "tasks"), budget_usd: cap(null, 25, "tasks", "work_item"), token_budget: cap(null, 2000000, "tasks", "work_item") },
    },
    work_item_usd: { ...leaf(10), binding: { key: "budget.work_item_usd", value: 10 } },
    daily_usd: leaf(50),
    max_attempts: { default: leaf(3), maximum: leaf<number>(null) },
    timeout_minutes: { default: leaf(60), maximum: leaf(180) },
  },
  loops: {
    default: { attempts: 3, wall_clock_s: 3600 },
    entries: [{ key: "verification.fix_loop", attempts: 2, wall_clock_s: 3600, live: true }, { key: "typo.fix_loop", attempts: 4, wall_clock_s: 600, live: false }],
    live: [
      { key: "verification.fix_loop", chain: "default", node: "verification", attempts: leaf(2, "loops"), wall_clock_s: leaf(3600, "loops") },
      { key: "merge_request_feedback.fix_loop", chain: "default", node: "merge_request_feedback", attempts: leaf(3, "default"), wall_clock_s: leaf(3600, "default") },
    ],
  },
  escalation: { auto_escalate_stuck: leaf(true), auto_escalate_stuck_cap: leaf(3), auto_escalate_delay_s: leaf(0), auto_review_attempts: leaf(1) },
  retries: { rate_limit_retries: leaf(5), forge_cli_timeout_s: leaf(120) },
  housekeeping: { max_concurrent: leaf(5), archive_after_days: leaf(30), storage_limit: leaf(null), storage_quota: leaf(null), storage_quota_default: null, storage_auto_cleanup: leaf(null) },
  findings: { loop_severities: leaf(["critical", "important"]) },
} as unknown as PolicyResolved;

export function policyView(over: Partial<Result> = {}, resolved: PolicyResolved | null = RESOLVED, draft = false): DraftView {
  return {
    area: "policy",
    key: "policy",
    draft,
    files: { "policy.yaml": "budget: {}\n" },
    base: { "policy.yaml": "x" },
    published: { "policy.yaml": "budget: {}\n" },
    updated_at: null,
    result: { model: { "policy.yaml": { budget: { work_item_usd: 10 } } }, problems: [], sources: {}, changes: [], warnings: [], policy_values: { auto_escalate_delay_s: 0, auto_review_attempts: 1 }, impact: null as never, resolved: resolved as never, ...over },
  };
}

export const problem = (p: Partial<Problem> & { message: string }): Problem => ({ path: "", field: null, file: "policy.yaml", line: 1, col: 1, ...p });
