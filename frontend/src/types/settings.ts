/* ── settings (design 5a–5e) ─────────────────────────────────────────────── */

export interface TestScope {
  paths: string[];
  command: string;
}

export interface Repo {
  path: string;
  name: string;
  default_chain_template: string;
  test_command: string | null;
  test_scopes: TestScope[] | null;
  forge: string | null;
  project: string | null;
  enabled: boolean;
  default_model: string | null;
  deny_tools: string[];
  steering: string[];
  default_root_merge_policy: "bump" | "skip" | "bump_no_mr";
  /** A human has touched this entry — not "this has run". One-way: never
   *  returns to false. Drives the Detected section in ReposPage. */
  managed: boolean;
}

export interface RepoProbe {
  path: string;
  name: string;
  branch: string | null;
  submodules: string[];
  has_beads: boolean;
  beads_export_auto: boolean;
  beads_export_git_add: boolean;
  has_engineering: boolean;
  test_command: string | null;
  test_scopes: TestScope[] | null;
  forge: string | null;
  project: string | null;
}

export interface TemplateNode {
  id: string;
  tasks: string[];
  gate_after: string | null;
  fix_loop?: string | null;
  on_failure?: string[];
  reject_to?: string | null;
  auto_escalate?: boolean;
  auto_escalate_stuck?: boolean;
  auto_escalate_delay_s?: number;
  /** Any node key the form doesn't render (e.g. `rebase_bounce_to` on
   *  `pre_mr_rebase` in `default.yaml`) still round-trips: the serializer
   *  writes every own-key of a node object, known or not, so editing one
   *  node never silently drops a key this form doesn't know about. */
  [key: string]: unknown;
}

export interface TemplateSummary {
  id: string;
  nodes: TemplateNode[];
  gates: number;
}

export interface TemplateValidation {
  id: string;
  valid: boolean;
  error: string | null;
  /** Every node task that is not in the registry, named by node. */
  unresolved: { node: string; task: string }[];
}

export interface HookBinding {
  kind: "builtin" | "agent" | "subprocess" | "forge";
  handler?: string;
  command?: string | string[];
  interactive?: boolean;
  timeout?: number;
  repos?: Record<string, { enabled: boolean; command?: string | string[] | null }>;
  /** Agent kind only. Steering files' "who uses it" (Steering page) reads
   *  this to name a hook the same way it names a repo. */
  steering?: string[];
}

export interface HookRun {
  work_item_id: string;
  node_id: string;
  round: number;
  status: string;
  wall_ms: number | null;
  created_at: string;
}

export interface SteeringFile {
  name: string;
  bytes: number | null;
}

export interface SteeringList {
  files: SteeringFile[];
  max_bytes: number;
}

export interface Intake {
  enabled: boolean;
  interval_s: number;
  // Moved to Policy.max_concurrent; kept optional here so an old client
  // reading/writing this file still round-trips.
  max_concurrent?: number;
  priority_ceiling: number;
  repos: string[];
  repo_pickups: Record<string, { items: number | null; last_picked_up: string | null }>;
  recent_pickups: {
    work_item_id: string;
    bead_id: string | null;
    title: string | null;
    repo: string | null;
    priority: number | null;
    status: string;
    at: string;
  }[];
}

export interface Cap {
  attempts: number;
  wall_clock_s: number;
}

export interface Policy {
  loops: Record<string, Cap>;
  default: Cap;
  findings?: { loop_severities?: string[] };
  budget?: { work_item_usd: number | null; daily_usd: number | null };
  max_concurrent: number;
  rate_limit_retries?: number;
  auto_escalate_stuck?: boolean;
  auto_escalate_stuck_cap?: number;
  auto_escalate_delay_s?: number;
  /** Days after completion/abandonment before `archive_poller` archives an
   *  item automatically; `after_days: null`/absent disables it. */
  archive?: { after_days: number | null } | null;
}

export type PaletteId = "nocturne" | "rose" | "forest" | "amber" | "slate";
export type ThemeMode = "light" | "dark" | "system";

/** `GET/PUT /theme`. Instance-wide, like every other Settings-backed value —
 *  see the theme-palettes design doc for why this isn't per-user. */
export type BoardGroupBy = "status" | "repo" | "template";
export type BoardOpenIn = "peek" | "full";

export interface Theme {
  palette: PaletteId;
  mode: ThemeMode;
  density: "compact" | "comfortable";
  board: { group_by: BoardGroupBy; show_done: number; open_in: BoardOpenIn };
}

export interface Access {
  bind: string;
  port: number;
  session_expiry_days: number;
  password_set: boolean;
  auth_required: boolean;
  allowed_hosts: string[];
}

/** `GET /notify`. The webhook URL is deliberately absent — the server never
 *  sends it back, so there is nothing here to accidentally render. */
export interface Notify {
  enabled: boolean;
  url_set: boolean;
  base_url: string | null;
  events: string[];
  last_test: { at: string; status: number | null; ms: number | null; error: string | null } | null;
}

export interface AuthSession {
  id: string;
  label: string | null;
  ip: string | null;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
}

export interface RepoRow {
  repo: string;
  path: string;
  role: "root" | "submodule";
  merge_rank: number;
  state: string;
}
