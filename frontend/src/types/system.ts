export interface Health {
  status: "ok" | "degraded";
  invalid_templates: Record<string, string>;
  invalid_policy: string[];
  /** Null without `storage.worktrees.limit`. `held`: starts that need a new worktree are waiting on disk space. */
  storage?: { state: "ok" | "over_quota" | "held"; used_bytes: number; quota_bytes: number; limit_bytes: number; measured_at: string } | null;
  /** Where the server is listening — the login screen tells the user. */
  bind?: string;
  /** Paired with `bind` for the sidebar footer ("127.0.0.1:8765"); absent on
   *  an older server that hasn't picked up this field yet. */
  port?: number;
  /** The Access page's session length, needed for the login screen's "stay
   *  signed in · N days" before a session exists to ask `/access` for it.
   *  Absent on an older server that hasn't picked up this field yet. */
  session_expiry_days?: number;
  /** The version this server process is running, or "0.0.0+source" for a
   *  checkout that was never installed. Sidebar footer only. */
  version?: string;
  /** The version installed on disk, which a restart would run. It differs from
   *  `version` between `kraft admin update` and the restart that finishes it.
   *  Absent on an older server, whose `version` was the installed one. */
  installed?: string;
  /** The directory holding this instance's databases, logs and worktrees. */
  run_dir?: string;
  pid?: number;
  /** Seconds this server process has been up (UX V2 About); absent on an older server. */
  uptime_s?: number;
  /** The search index: how many documents, when the repos were last scanned, and what failed. */
  index?: { documents: number; last_scan_at: string | null; errors: string[] };
  /** Whether this request carries a live session (Kraft-yx79s): lets a
   *  locked instance open on Login without a 401 probe. Absent on a server
   *  that does not send it yet, and then main.tsx probes /api/theme. */
  authenticated?: boolean;
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

/** `GET /storage`: the cached measurement of the run folder joined to the work items. Null `state`, `quota_bytes` and `limit_bytes` mean no `storage.worktrees.limit` is set. */
export type StorageState = "ok" | "over_quota" | "held";
export interface StorageItem {
  id: string;
  title: string;
  status: string;
  archived: boolean;
  bytes: number;
  updated_at: string;
  /** Completed or abandoned, and not archived: what a clean-up may take. */
  reclaimable: boolean;
}
export interface StorageUsage {
  measured_at: string;
  state: StorageState | null;
  used_bytes: number;
  quota_bytes: number | null;
  limit_bytes: number | null;
  reclaimable_bytes: number;
  /** worktrees, sandboxes, logs, results, databases, attachments, other. */
  categories: Record<string, number>;
  /** Largest first. */
  items: StorageItem[];
  /** Worktree folders with no work item row. */
  orphans: { name: string; bytes: number }[];
}

/** `POST /storage/preview`: what archiving the ids would do. It changes nothing. */
export interface StoragePreviewItem {
  id: string;
  title: string;
  bytes: number;
  archivable: boolean;
  /** Why not, when `archivable` is false. */
  refusal: string | null;
  /** Null when the item has no worktree. */
  uncommitted_files: number | null;
  unpushed_commits: number;
  /** True when the branch has commits nothing else holds, so archiving keeps it. */
  branch_kept: boolean;
}
export interface StoragePreview {
  freed_bytes: number;
  used_after_bytes: number;
  state_after: StorageState | null;
  items: StoragePreviewItem[];
}
