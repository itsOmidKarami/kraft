import type { Result } from "../../templates/draft/types";

/** `{value, source}`: a policy value and where it comes from (`policy` or `default`; a cap's `source` is its level). */
export interface Leaf<T = number | string | boolean | string[]> {
  value: T | null;
  source: string | null;
}

/** What sets a cap below policy (W15 A.4). */
export interface Below {
  layer: "chain" | "library";
  chain: string;
  path: string;
  via: string | null;
  value: number;
  exceeds: boolean;
}

export interface CapView {
  default: Leaf<number>;
  maximum: Leaf<number>;
  below: Below[];
}

export interface LiveLoop {
  key: string;
  chain: string;
  node: string;
  attempts: Leaf<number>;
  wall_clock_s: Leaf<number>;
}

export interface PolicyResolved {
  limits: {
    caps: Record<string, Record<string, CapView>>;
    work_item_usd: Leaf<number> & { binding: { key: string; value: number } | null };
    daily_usd: Leaf<number>;
    max_attempts: { default: Leaf<number>; maximum: Leaf<number> };
    timeout_minutes: { default: Leaf<number>; maximum: Leaf<number> };
  };
  loops: {
    default: { attempts: number; wall_clock_s: number };
    entries: { key: string; attempts: number; wall_clock_s: number; live: boolean }[];
    live: LiveLoop[];
  };
  escalation: Record<string, Leaf>;
  retries: Record<string, Leaf>;
  housekeeping: { max_concurrent: Leaf<number>; archive_after_days: Leaf<number>; storage_limit: Leaf<string>; storage_quota: Leaf<string>; storage_quota_default: string | null; storage_auto_cleanup: Leaf<string> };
  findings: { loop_severities: Leaf<string[]> };
}

/** `result.resolved` of a `policy` draft; null while the file does not load. */
export const policyOf = (r: Result): PolicyResolved | null => {
  const v = r.resolved as unknown as Partial<PolicyResolved> | null;
  return v && v.limits && v.loops ? (v as PolicyResolved) : null;
};

/** The cap levels in the order the cards draw them, and the cap fields' labels. */
export const LEVELS = ["work_item", "nodes", "steps", "tasks"] as const;
export const LEVEL_LABEL: Record<string, { name: string; sub: string }> = {
  work_item: { name: "work item", sub: "the whole item" },
  nodes: { name: "nodes", sub: "each node or gate run" },
  steps: { name: "steps", sub: "each step run" },
  tasks: { name: "tasks", sub: "each task run" },
};
export const CAP_LABEL: Record<string, { name: string; unit: Unit }> = {
  time_cap_minutes: { name: "running", unit: "min" },
  total_time_cap_minutes: { name: "wall clock", unit: "min" },
  budget_usd: { name: "dollars", unit: "usd" },
  token_budget: { name: "tokens", unit: "tok" },
};
export type Unit = "min" | "usd" | "tok" | "days" | "s" | "count" | "s-as-min" | "size" | "age";
