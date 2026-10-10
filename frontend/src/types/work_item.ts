import type { RepoRow } from "./settings";
import type { DisplayStatus, EventType, SessionStatus, StopKind, WorkItemStatus } from "./vocab.generated";

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
   *  chain (`store.node_view`); `tasks` is the same list flattened, in
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
   *  (`store.node_view`: the node's own recovery pass). */
  on_failure?: string[] | null;
  /** The node's own Lucide icon name, null when it sets none (always on a
   *  gate). The chain editor reads it. */
  icon?: string | null;
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
    model?: string | null;
    escalate_model?: string | null;
    effort?: string;
    /** Added after each agent task's own prompt in the node. */
    extra_prompt?: string | null;
  }
>;

/** Where the implementer is in its plan (Kraft-qqz8): "3 of 6 · title", derived
 *  server-side from the plan's `## Task N` headings, the latest
 *  `plan_progress` report and the highest task a commit subject names
 *  (`progress.combine`). `null`/absent for a plan with no headings. The list
 *  endpoint (`_board_progress`) sends it only while the implementation node
 *  runs, without `tasks`, for the board row's "Task 3/6" line; the detail
 *  (`progress.for_detail`) keeps it once the node stops or finishes, every
 *  task `done` after it, each with the short `sha` of the newest commit
 *  naming it. */
export interface TaskProgress {
  current: number;
  total: number;
  title: string;
  tasks?: { n: number; title: string; state: "done" | "current" | "pending"; sha?: string | null }[];
}

/** The Config tab's "$5.00 · $2.41 used" line and its `policy default` /
 *  `item` source tag (UI v2 · 04 point 4). Named `budget_cap`, not `budget`
 *  -- `WorkItem.budget` is a different, event-derived field (see there). */
export interface BudgetCap {
  /** The lower of the item's own cap and its chain policy's item-wide
   *  `budget_usd` (which a stop's Raise cap writes into `policy_override`). */
  cap_usd: number | null;
  source: "item" | "policy";
  /** The `PATCH` field that changes `cap_usd`: the item's own `budget_usd`, or its policy's. */
  key?: "budget_usd" | "policy.budget_usd";
  spent_usd: number;
  /** The instance's spend since local midnight against `policy.budget.daily_usd`
   *  (B10, `GET /budget/today`'s own shape) -- optional/unused by the shipped
   *  UI, which has no daily total anywhere yet. */
  daily?: { spent_usd: number; cap_usd: number | null };
}

/** `GET /budget/today` (B10): the instance's spend since local midnight
 *  against `policy.budget.daily_usd`, with no single work item in view.
 *  Optional/unused by the shipped UI. */
export interface BudgetToday {
  spent_usd: number;
  cap_usd: number | null;
}

export interface ChainDefinition {
  template_id: string;
  /** Always sent by the server (`store.chain_view` fills it for either chain
   *  shape), but read through a `?? []` at every use: the field was `{}` on a
   *  V1 row for one release, and a blank board is the failure mode. */
  nodes: ChainNode[];
}

export type { WorkItemStatus };

export interface WorkItemAttachment {
  kind: "spec" | "plan";
  /** Repo-relative path, normalized by the server. */
  path: string;
}

/** Kraft UI v2 · B1's badge, derived server-side (`board.display_status`):
 *  exactly one of these, so a pending gate, a plain failure and a
 *  stuck-but-not-yet-escalated stop -- all `needs_human` in `status` -- read
 *  apart with no client-side derivation. */
export type { DisplayStatus };

/** The kinds the server sends: the generated vocabulary, nothing added by hand. */
export type { StopKind };

/** `stop` on a work item response (B.3/B.4): `null` unless `status` is
 *  `needs_human`, `waiting` or `rate_limited`. The list omits `task`,
 *  `attempt` and `facts`; only the detail endpoint sends them. */
/** What raises the limit a `cap` or `budget` stop hit (detail only; absent when no `PATCH policy` can):
 *  `path` is `""` for the item-wide cap, else the fix loop's node id. `budget_usd` is a policy dollar cap, not the item's own. */
export interface StopLimit {
  path: string;
  key: "max_attempts" | "timeout_minutes" | "time_cap_minutes" | "total_time_cap_minutes" | "budget_usd";
  value: number;
  maximum: number | null;
}

export interface WorkItemStop {
  kind: StopKind;
  node: string | null;
  task?: string | null;
  attempt?: number | null;
  resume_at: string | null;
  reason: string | null;
  facts?: Record<string, unknown>;
  limit?: StopLimit;
  /** A `budget` stop on the detail: which cap stopped it. `work_item`, the
   *  item's own, is the one `POST /work-items/{id}/budget/raise` takes. */
  scope?: BudgetStop["scope"];
}

/** `GET /work-items/{id}/cancel-preview` (B4): what `POST .../cancel` would
 *  do, read-only. The Cancel and Mark complete cards read it; `beads` is
 *  every bead Mark complete's box would close. */
export interface CancelPreview {
  running: { node: string | null; task: string | null; attempt: number | null; started_at: string | null } | null;
  kept: { branch: string; worktree: string; findings: number; threads: number };
  mr: { ref: number; url: string; state: "open" | "merged" | "closed" } | null;
  spend: { spent_usd: number; cap_usd: number | null };
  beads: string[];
}

/** `POST /work-items/{id}/duplicate` (B3): a fresh, paused item from this
 *  one's own title, description, repo, chain, workspace selection
 *  and attachments. Additive: not read by the shipped UI yet. */
export interface DuplicateResponse {
  id: string;
  status: "paused";
  duplicate_warning?: string;
}

/** One id's outcome in a `POST /work-items/bulk` (B9) batch, in request order. */
export interface BulkResult {
  id: string;
  ok: boolean;
  status?: WorkItemStatus;
  error?: string;
}

/** `POST /work-items?dry_run=1` (B33): what create would do, without doing
 *  it. Additive: not read by the shipped UI yet. */
export interface CreateDryRun {
  dry_run: true;
  nodes: ChainNode[];
  skipped: (
    | { node: string; why: "covered_by"; kind: string }
    | { node: string; why: "skip" }
  )[];
  gates: string[];
  caps: {
    budget_usd: number | null;
    budget_source: "item" | "policy";
    daily_usd: number | null;
    nodes: Record<string, { attempts: number; wall_clock_s: number }>;
  };
}

/** The breach a spend-cap stop recorded (`kraft.caps.Breach`), tagged on
 *  `scope`. Only `work_item` is the item's own cap, the one Raise budget raises. */
export type BudgetStop =
  | { scope: "work_item" | "daily"; spent_usd: number; cap_usd: number }
  | { scope: "usd"; path: string; spent_usd: number; cap_usd: number; unknown_launches: number }
  | { scope: "tokens"; path: string; spent_tokens: number; cap_tokens: number };

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
  /** Null for an item filed with no chain: the repo's default chain runs (see ng/item/chainName). */
  chain_template: string | null;
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
  /** Where a request-changes review at the pending gate restarts the chain,
   *  null with no gate pending. `round` is the one a rejection now would start. */
  fix_target?: FixTarget | null;
  /** The pending gate's attempts, oldest first; empty with no gate pending
   *  (the compare picker's `attempt:N` answers only then, R43). */
  attempts?: GateAttempt[];
  /** HEAD at the last submitted review (R20: the instance's, no identity); null before one. */
  last_review_sha?: string | null;
  /** A gateless request-changes waiting to be honoured at its target node. */
  pending_rewind?: { seq: number; review_id: string; target: string; note?: string } | null;
  /** The `launch_fallback` payload when the item's current or last launch ran
   *  on a fallback candidate (Kraft-0a3h8); null otherwise. */
  fallback?: Record<string, unknown> | null;
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
  budget?: BudgetStop | null;
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
  /** Whether `worktree_path` is on disk: false once it was reclaimed or
   *  deleted (and before the item first ran). Only on the detail endpoint. */
  worktree_exists?: boolean;
  /** The branch the item's worktree lives on (detail only): `kraft/<slug>-<id>`, or `kraft/<id>` on an older row. */
  branch?: string | null;
  /** The worktree's current HEAD (Kraft-lu2), so the gate can tell a
   *  measurement taken on this commit from one taken before it. Only on the
   *  detail endpoint. */
  head_sha?: string | null;
  /** why the item is stopped, from the `work_item_needs_human` it sits on */
  stop_reason?: string | null;
  /** The latest changed-test-scope verification run, one entry per scope that
   *  finished; null when there is no run. Only on the detail endpoint. */
  test_result?: TestResult | null;
  /** Every command a changed-test-scope task ran, over every round and repository, in the order it
   *  started (detail only). `test_result` is the latest run alone. */
  scope_runs?: ScopeRun[];
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
   *  On the detail and the list. */
  mr_ref?: { number: number; url: string } | null;
  /** Only on the detail endpoint; see `TaskProgress`. */
  progress?: TaskProgress | null;
  /** `chain_definition` with `node_overrides` folded over each node -- what
   *  actually runs. Only on the detail endpoint (UI v2 · 04 point 3). */
  effective_chain?: ChainDefinition;
  /** This item's own per-node field overrides, `{}` when there are none.
   *  Only on the detail endpoint. */
  node_overrides?: NodeOverrides;
  /** This item's own model and effort for every agent task, null when it sets none. */
  agent_overrides?: { model?: string | null; escalate_model?: string | null; effort?: string } | null;
  /** The chain intake froze for this item, as one JSON document
   *  (`MaterializedChain.to_json`). Only on the detail endpoint. */
  materialized_chain?: string | null;
  /** `Object.keys(node_overrides).length` -- the Config tab's "default + N
   *  overrides" count. Only on the detail endpoint. */
  node_overrides_count?: number;
  /** This item's effective spend cap and its source. Only on the detail
   *  endpoint. */
  budget_cap?: BudgetCap;
  /** The item's time budget (detail only): what its item-wide `time_cap_minutes` has measured, and that cap (null: none). */
  running_time?: { running_s: number; cap_minutes: number | null } | null;
  /** Whether the item set its own dollar cap, and that cap (null: no cap).
   *  Unset, the policy's `work_item_usd` holds it. Only on the detail endpoint. */
  budget_set?: number | boolean;
  budget_usd?: number | null;
  /** The item's own policy override: item-wide fields, and `paths` for one
   *  node, step or task's. Null when it has none. Only on the detail endpoint. */
  policy_override?: (Record<string, unknown> & { paths?: Record<string, Record<string, unknown>> }) | null;
  /** UI v2 · 06 rate-limited sub-row: relaunches used vs `policy.rate_limit_retries`.
   *  null off a non-rate_limited item. Only on the detail endpoint. */
  rate_limit?: { count: number; cap: number } | null;
  /** Whether an agent may review this item's `auto_escalate` gates before a
   *  human sees them (Kraft-zr3s). Set at intake; the column is on every row. */
  auto_gate?: boolean;
  /** What the item comes after, declared at intake; `met` once that one
   *  completed. Only on the detail endpoint. */
  dependencies?: { id: string; title: string; status: WorkItemStatus; met: boolean }[];
  /** The board's status badge (Kraft UI v2 · B1). */
  display_status?: DisplayStatus;
  /** The stop `display_status` is reporting on; `null` off `needs_human`,
   *  `waiting` and `rate_limited`. Additive, unread by the shipped UI. */
  stop?: WorkItemStop | null;
  /** A run's progress at a glance (Kraft UI v2 · B13). Only on the detail
   *  endpoint. Additive, unread by the shipped UI. */
  summary?: WorkItemSummary;
  /** The current node's step out of its steps, with the step's id and the
   *  latest task's (absent when the task's path has no step or task segment), when the node declares more than one step and a session
   *  has run. List only; `summary.step` is the detail's. */
  step?: { index: number; count: number; name?: string; task?: string } | null;
}

/** `GET /work-items/{id}`'s `summary` (B13): nodes done out of the frozen
 *  chain's total, how many of those were gates, and the current node's step
 *  (1-based) out of its steps when it declares more than one. */
export interface WorkItemSummary {
  nodes_done: number;
  nodes_total: number;
  gates_passed: number;
  step: { index: number; count: number } | null;
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

export type { SessionStatus };

/** One command a changed-test-scope task ran: the session that ran it, and what the repo's table says
 *  it is. `passed` is null until it finishes; `order` is where the table lists it (an area's setup
 *  half a place before its first scope), absent for a command the table no longer declares. */
export interface ScopeRun {
  /** Null for a command its round picked and has not started (`pending`). */
  session_id: string | null;
  node_id: string;
  hook_point: string;
  repository: string | null;
  round: number;
  command: string;
  passed: boolean | null;
  exit_code?: number;
  scope?: string;
  area?: string;
  setup?: true;
  order?: number;
  /** Picked by its round (`test_scopes_selected`) and not started: no session yet. */
  pending?: true;
  /** Its round recorded what it picked, so what it dropped is known before the round ends. */
  selected?: true;
}

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
  /** Cache kinds apart from tokens_in (Ruling 211); null on a row from before. */
  tokens_cache_write?: number | null;
  tokens_cache_read?: number | null;
  cost_usd: number | null;
  /** True while `cost_usd` is a running guess from live tokens (Kraft-wz83s),
   *  not the agent's own figure; false once it's settled at exit or was
   *  never estimated. Optional so fixture/test literals that predate it
   *  keep typechecking. */
  cost_estimated?: boolean;
  wall_ms: number | null;
  model: string | null;
  /** The harnesses.yaml profile an agent session ran on; null
   *  for a subprocess or builtin task and on a row from before the column.
   *  Optional so fixture/test literals that predate it keep typechecking. */
  harness?: string | null;
  /** The exact command a subprocess session ran; null for any other kind. Optional so fixture literals that predate it typecheck. */
  command?: string | null;
  /** The repository a fanned-out run (`scope: each_repository`) was for; null for a task that does not fan out. */
  repository?: string | null;
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
  /** One of the server's event types. A compile-time aid only: an SPA older
   *  than its server still receives types it does not know. */
  type: EventType;
  payload: Record<string, unknown>;
  /** The node this event is about, or `null` for an item-level event (Kraft
   *  UI v2 · B13). Defaulted server-side from the payload's own `node_id`/
   *  `node` key, or set explicitly by an emitter that knows its node. */
  node_id?: string | null;
  created_at: string;
}

export interface UsageRollup {
  tokens_in: number;
  tokens_out: number;
  tokens_cache_write?: number;
  tokens_cache_read?: number;
  /** False when some session predates the split: its cache use is in tokens_in. */
  split_complete?: boolean;
  /** False when some session spent tokens but has no output count yet: tokens_out is a floor. */
  out_complete?: boolean;
  cost_usd: number;
  /** False when a session spent tokens but reported no cost — the sum is a floor. */
  cost_complete: boolean;
  /** True when any session folded in is still running on an estimated cost
   *  (Kraft-wz83s) rather than a settled figure. */
  cost_estimated?: boolean;
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
  /** present only on the marker row (`n: -1`) a reader gets in place of the
   *  lines it skipped, when the log is over the server's read cap */
  truncated?: { lines: number; bytes: number };
}

export interface GateAttempt {
  n: number;
  sha: string;
  base_sha: string;
  at: string;
}

export interface FixTarget {
  gate: string | null;
  node: string;
  /** The nodes between `node` and the gate (or through the current node, gateless), in order. */
  then: string[];
  round: { n: number; max: number } | null;
  /** Only on GET /work-items/:id/fix-target: `gate`, `requested`, `threads on <file>` or `current node`. */
  reason?: string;
}

export interface TestResult {
  scopes: { command: string; scope: string | null; passed: boolean; exit_code: number | null; session_id: string }[];
  passed: boolean;
}
