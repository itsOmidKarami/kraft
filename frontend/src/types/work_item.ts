import type { RepoRow } from "./settings";

export interface ChainNode {
  id: string;
  /** `exec` or `gate` on a Template Schema V1 chain, absent on a legacy one.
   *  A V1 gate *is* a node of its own rather than a `gate_after` string on the
   *  node in front of it, so this is how the two are told apart -- never by
   *  faking `gate_after` onto the gate node. */
  kind?: "exec" | "gate";
  /** On a V1 node, the attachment kind that drops it at intake -- the gate
   *  deciding that document and the node that would write it
   *  (`ResolvedNode.covered_by`, Kraft-ene04). Absent on a legacy node. */
  covered_by?: string | null;
  tasks: string[];
  /** Ordered groups of concurrent tasks. Always present on a materialized
   *  chain (`templates.with_steps`); `tasks` is the same list flattened, in
   *  group order, and every other consumer reads that instead. */
  steps?: string[][];
  gate_after: string | null;
  fix_loop?: string;
  /** Where rejecting this node's gate sends the chain; null re-runs this node. */
  reject_to?: string | null;
  /** Where a rebase-triggered bounce (Kraft-4bgg) sends the chain back to
   *  re-verify; null on a node that isn't a rebase check. */
  rebase_bounce_to?: string | null;
  /** Whether an agent may review this node's gate before a human sees it
   *  (Kraft-zr3s). Only meaningful beside `gate_after`. */
  auto_escalate?: boolean | null;
  /** Whether a `needs_human` stop that is *not* a pending gate — a fix loop
   *  out of attempts — auto-dispatches an escalation turn. Independent of
   *  `auto_escalate`: different mechanism, different trigger. */
  auto_escalate_stuck?: boolean | null;
  /** Seconds to hold either escalation back after its triggering event, so a
   *  human already on the way isn't preempted. `0` fires immediately. */
  auto_escalate_delay_s?: number | null;
  /** Hook points run to repair a red measurement before the fix loop retries
   *  (templates.py `NODE_CARRYOVER_FIELDS`). */
  on_failure?: string[] | null;
}

/** A node id's overridden fields, from the item's own `node_overrides`
 *  (UI v2 · 04 point 1; `attempts`/`wall_clock_s` added Kraft-439u.2). */
export type NodeOverrides = Record<
  string,
  {
    auto_escalate?: boolean;
    auto_escalate_stuck?: boolean;
    auto_escalate_delay_s?: number;
    attempts?: number;
    wall_clock_s?: number;
  }
>;

/** Where the implementer is in its plan (Kraft-qqz8): "3 of 6 · title", derived
 *  server-side from the plan's `## Task N` headings, the latest
 *  `plan_progress` report and the highest task a commit subject names
 *  (`progress.combine`). `null`/absent off the implementation node or for a
 *  plan with no headings. The list endpoint (`_board_progress`) sends it too,
 *  without `tasks`, for the board row's "Task 3/6" line. */
export interface TaskProgress {
  current: number;
  total: number;
  title: string;
  tasks?: { n: number; title: string; state: "done" | "current" | "pending" }[];
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
  /** Always sent by the server (`store.chain_view` fills it for either chain
   *  shape), but read through a `?? []` at every use: the field was `{}` on a
   *  V1 row for one release, and a blank board is the failure mode. */
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
  // Parked on a pipeline that has not settled; the wait scheduler re-enters
  // the node when retry_at comes due, no human paged (Kraft-ru98).
  | "waiting";

export interface WorkItemAttachment {
  kind: "spec" | "plan";
  /** Repo-relative path, normalized by the server. */
  path: string;
}

export interface EscalationThread {
  thread: number;
  session_id: string;
  turns: number;
  started_at: string;
  ended_at: string | null;
  status: SessionStatus;
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
  /** Set once the item has been archived (UI v2 · 03); null otherwise. Status
   *  never changes on archive — "Ended as" keeps reading completed/abandoned. */
  archived_at?: string | null;
  archived_by?: "you" | "auto" | null;
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
  /** One entry per escalation thread this item has had, oldest first
   *  (Kraft-dkb6g). Only on the detail endpoint. */
  escalation_threads?: EscalationThread[];
  worktree_path?: string;
  /** The worktree's current HEAD (Kraft-lu2), so the gate can tell a
   *  measurement taken on this commit from one taken before it. Only on the
   *  detail endpoint. */
  head_sha?: string | null;
  /** why the item is stopped, from the `work_item_needs_human` it sits on */
  stop_reason?: string | null;
  /** Minor findings that never entered the fix loop; only on the detail endpoint. */
  deferred_findings?: Finding[];
  /** Findings a judge chose to stop chasing (`stop_downgrade`) -- distinct
   *  from `deferred_findings`: these are critical/important, not minor ones
   *  that never entered the loop. Only on the detail endpoint. */
  judge_stop_note?: { node_id: string; reasoning: string; findings: Finding[] }[];
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
  /** UI v2 · 06 rate-limited sub-row: relaunches used vs `policy.rate_limit_retries`.
   *  null off a non-rate_limited item. Only on the detail endpoint. */
  rate_limit?: { count: number; cap: number } | null;
  /** Whether an agent may review this item's `auto_escalate` gates before a
   *  human sees them (Kraft-zr3s). Set at intake; the column is on every row. */
  auto_gate?: boolean;
}

export interface Finding {
  severity: string;
  message: string;
  file: string | null;
  line: number | null;
  source_plugin: string;
  /** What the reviewer itself rated this, when the fix loop overrode it.
   *  A repeat finding cannot be re-rated downwards on a tree nobody touched
   *  (Kraft-s7c04.3); the reviewer's own answer is kept rather than erased, so
   *  a reviewer disagreeing with itself stays visible rather than becoming
   *  indistinguishable from a reviewer agreeing. Absent when they match. */
  reported_severity?: string;
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
  // wait scheduler re-enters it when retry_at comes due (Kraft-ru98).
  | "waiting";

export interface WorkerSession {
  id: string;
  work_item_id: string;
  node_id: string;
  hook_point: string;
  status: SessionStatus;
  attempt: number;
  /** 1-based; restarts only across a `new_thread` escalation (Kraft-dkb6g).
   *  Every non-escalation session is implicitly thread 1 for its whole life. */
  thread: number;
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
  /** Path (relative to repo) the worker's result reported as its
   *  `.engineering/sessions/*.md` writeup (03 §3); optional so the many
   *  fixture/test literals that predate this field keep typechecking. */
  session_summary_ref?: string | null;
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
