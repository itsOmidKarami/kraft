import type { RepoRow } from "./settings";

export interface ChainNode {
  id: string;
  tasks: string[];
  gate_after: string | null;
  fix_loop?: string;
  /** Where rejecting this node's gate sends the chain; null re-runs this node. */
  reject_to?: string | null;
  /** Whether an agent may review this node's gate before a human sees it
   *  (Kraft-zr3s). Only meaningful beside `gate_after`. */
  auto_escalate?: boolean | null;
}

/** A node id's overridden fields, from the item's own `node_overrides`
 *  (UI v2 · 04 point 1). Today only `auto_escalate` is supported. */
export type NodeOverrides = Record<string, { auto_escalate?: boolean }>;

/** Where the implementer is in its plan (Kraft-qqz8): "Task 3 of 6", derived
 *  server-side from the plan's `## Task N` headings, the latest
 *  `task_progress` report and the highest task a commit subject names
 *  (`progress.combine`). `null`/absent off the implementation node or for a
 *  plan with no headings. */
export interface TaskProgress {
  current: number;
  total: number;
  title: string;
  tasks: { n: number; title: string; state: "done" | "current" | "pending" }[];
}

/** The Config tab's "$5.00 · $2.41 used" line and its `policy default` /
 *  `item` source tag (UI v2 · 04 point 4). Named `budget_cap`, not `budget`
 *  -- `WorkItem.budget` is a different, event-derived field (see there). */
export interface BudgetCap {
  cap_usd: number | null;
  source: "item" | "policy";
  spent_usd: number;
}

export interface ChainDefinition {
  template_id: string;
  nodes: ChainNode[];
}

export type WorkItemStatus =
  | "active"
  | "needs_human"
  | "completed"
  | "paused"
  // Terminal, and off the board unless explicitly asked for (Kraft-x85).
  | "abandoned"
  // Waiting on an API rate limit to reset; the poller relaunches it, no
  // human paged.
  | "rate_limited"
  // Parked on a pipeline that has not settled; the ci_wait poller re-enters
  // the node when retry_at comes due, no human paged (Kraft-ru98).
  | "waiting";

export interface WorkItemAttachment {
  kind: "spec" | "plan";
  /** Repo-relative path, normalized by the server. */
  path: string;
}

export interface WorkItem {
  id: string;
  title: string;
  /** The brief, in prose. Prepended to every agent's task instruction; the
   *  title alone is only a label. */
  description?: string | null;
  repo: string;
  status: WorkItemStatus;
  chain_template: string;
  chain_definition: ChainDefinition;
  current_node_id: string | null;
  bead_id: string | null;
  /** Steer text left while paused; consumed by the next agent launch. */
  pending_steer_context?: string | null;
  /** Set while `status` is `"rate_limited"` or `"waiting"`: when the poller
   *  next acts on this item. */
  retry_at?: string | null;
  created_at: string;
  updated_at: string;
  /** The gate waiting on a person, straight from the server — a rejected gate
   *  is not pending, which no client-side inference from sessions can see. */
  pending_gate?: string | null;
  /** Repo-relative path to the document the pending gate is a decision about,
   *  or null when the agent wrote nothing for a human to review. */
  gate_artifact?: string | null;
  // client-derived, not from the list endpoint:
  rejectNote?: string | null;
  fixCycle?: number;
  completedNodes?: string[];
  /** Set when a loop cap is what stopped the item — the board shows "capped n/n". */
  cappedOut?: { cycles: number; attempts: number } | null;
  /** Set when a spend cap is what stopped the item (sub-project E §3). */
  budget?: { scope: "work_item" | "daily"; spent_usd: number; cap_usd: number } | null;
  /** Only on the detail endpoint, not the list. */
  usage?: WorkItemUsage;
  /** Empty on a single-repo item; ordered deepest submodule first. */
  repos?: RepoRow[];
  /** Documents attached at intake; the gates they satisfy are absent from the chain. */
  attachments?: WorkItemAttachment[];
  root_merge_policy?: string | null;
  worktree_path?: string;
  /** The worktree's current HEAD (Kraft-lu2), so the gate can tell a
   *  measurement taken on this commit from one taken before it. Only on the
   *  detail endpoint. */
  head_sha?: string | null;
  /** why the item is stopped, from the `work_item_needs_human` it sits on */
  stop_reason?: string | null;
  /** Minor findings that never entered the fix loop; only on the detail endpoint. */
  deferred_findings?: Finding[];
  /** `done_with_concerns` text from every session that reported one; only on the detail endpoint. */
  concerns?: string[];
  /** The agent's question, set only while a `needs_human` stop is answerable
   *  as a `needs_context` one; only on the detail endpoint. */
  needs_context_question?: string | null;
  /** Whether `current_node_id` has any agent task a retry's steer note could
   *  reach — false on e.g. `open_mr` (forge-kind, no fix_loop). `retry` 409s
   *  on explicit steer text otherwise, so the detail screen uses this to drop
   *  the steer box rather than offer one. Only on the detail endpoint. */
  steerable?: boolean;
  /** The root repo's merge request, once `open_mr` has run; null before then.
   *  Only on the detail endpoint. */
  mr_ref?: { number: number; url: string } | null;
  /** Only on the detail endpoint; see `TaskProgress`. */
  progress?: TaskProgress | null;
  /** `chain_definition` with `node_overrides` folded over each node -- what
   *  actually runs. Only on the detail endpoint (UI v2 · 04 point 3). */
  effective_chain?: ChainDefinition;
  /** This item's own per-node field overrides, `{}` when there are none.
   *  Only on the detail endpoint. */
  node_overrides?: NodeOverrides;
  /** `Object.keys(node_overrides).length` -- the Config tab's "default + N
   *  overrides" count. Only on the detail endpoint. */
  node_overrides_count?: number;
  /** This item's effective spend cap and its source. Only on the detail
   *  endpoint. */
  budget_cap?: BudgetCap;
}

export interface Finding {
  severity: string;
  message: string;
  file: string | null;
  line: number | null;
  source_plugin: string;
}

export type SessionStatus =
  | "pending"
  | "running"
  | "done"
  | "done_with_concerns"
  | "needs_context"
  | "failed"
  | "capped_out"
  | "paused"
  | "unknown"
  | "rate_limited"
  | "config_error"
  // A forge task (ci_poll) parked on a pipeline that has not settled; the
  // ci_wait poller re-enters it when retry_at comes due (Kraft-ru98).
  | "waiting";

export interface WorkerSession {
  id: string;
  work_item_id: string;
  node_id: string;
  hook_point: string;
  status: SessionStatus;
  attempt: number;
  /** Fix-cycle index this session was dispatched in; 0 on the first pass. */
  round: number;
  created_at: string;
  started_at: string | null;
  exited_at: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  wall_ms: number | null;
  model: string | null;
  /** The worktree HEAD this session was dispatched against (Kraft-lu2); null
   *  for a builtin/agent task that stamps nothing, and for a historical row. */
  head_sha: string | null;
}

export interface KraftEvent {
  seq: number;
  work_item_id: string;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface UsageRollup {
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  /** False when a session spent tokens but reported no cost — the sum is a floor. */
  cost_complete: boolean;
  wall_ms: number;
  sessions: number;
  rounds: number;
  capped_out: number;
}

export interface WorkItemUsage {
  total: UsageRollup;
  by_node: (UsageRollup & { node: string })[];
}

/** One line of a worker session's log (GET /worker-sessions/{id}/log?format=jsonl). */
export interface LogLine {
  n: number;
  t: string | null;
  src: "sys" | "stdout" | "agent" | "tool";
  /** the raw line, capped at 2000 characters by the server */
  text: string;
  /** a one-line rendering of a stream-json line; absent for plain output */
  summary?: string;
}
