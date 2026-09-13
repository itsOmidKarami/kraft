export interface Health {
  status: "ok" | "degraded";
  invalid_templates: Record<string, string>;
  invalid_policy: string[];
  /** Where the server is listening — the login screen tells the user. */
  bind?: string;
  /** Paired with `bind` for the sidebar footer ("127.0.0.1:8765"); absent on
   *  an older server that hasn't picked up this field yet. */
  port?: number;
  /** The Access page's session length, needed for the login screen's "stay
   *  signed in · N days" before a session exists to ask `/access` for it.
   *  Absent on an older server that hasn't picked up this field yet. */
  session_expiry_days?: number;
  /** This build's installed version, or "0.0.0+source" for a checkout that
   *  was never installed (`kraft.update.installed()`). Sidebar footer only. */
  version?: string;
}

export interface Analytics {
  totals: {
    work_items: number;
    work_items_run: number;
    by_status: Record<string, number>;
    mrs_merged: number;
    wall_ms: number;
    human_wait_ms: number;
    tokens_in: number;
    tokens_out: number;
    cost_usd: number;
    cost_complete: boolean;
    rounds: number;
    capped_out: number;
    completed: number;
    completed_prev: number | null;
    median_lead_ms: number;
    human_wait_pct: number;
    fix_cycles: number;
    fix_cycles_capped: number;
    rejected_gates: number;
    unplanned_touches_per_item: number;
    open_mr_to_green_ci_ms: number;
  };
  weekly_merged: { week_start: string; n: number }[];
  by_node: {
    node: string;
    runs: number;
    wall_ms: number;
    avg_ms: number;
    tokens: number;
    cost_usd: number;
    cost_complete: boolean;
    rounds: number;
    capped_out: number;
  }[];
  by_repo: {
    repo: string;
    items: number;
    mrs: number;
    tokens: number;
    cost_usd: number;
    cost_complete: boolean;
    done: number;
    cycles: number;
  }[];
  rejected_gates_by_gate: { gate: string; n: number }[];
  stop_reasons: { label: string; n: number }[];
}
