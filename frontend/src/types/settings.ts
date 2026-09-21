import type { ChainNode } from "./work_item";

/* ── settings (design 5a–5e) ─────────────────────────────────────────────── */

export interface TestScope {
  paths: string[];
  command: string;
}

export interface Repo {
  path: string;
  /** The repository id a workspace names this entry by; only a workspace's
   *  root and members carry one. */
  id?: string | null;
  name: string;
  default_chain_template: string;
  test_command: string | null;
  test_scopes: TestScope[] | null;
  forge: string | null;
  project: string | null;
  enabled: boolean;
  /** Model per harness profile id (Ruling 165), e.g. `{claude_review: "opus"}`. */
  models: Record<string, string>;
  deny_tools: string[];
  steering: string[];
  /** Relative file paths carried into every worktree before `uv sync`
   *  (Kraft-gxcmy) -- refused, not copied, if the repo does not gitignore
   *  the entry. */
  local_files: string[];
  /** A human has touched this entry — not "this has run". One-way: never
   *  returns to false. Drives the Detected section in ReposPage. */
  managed: boolean;
}

/** A `repos.yaml` workspace: a root repository and members mounted in it,
 *  each naming a repository by `Repo.id`. */
export interface Workspace {
  id: string;
  root: string;
  root_pointer_default: "ignore" | "bump";
  members: Record<string, { repository: string; path: string }>;
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

/** One saved chain as `GET /templates` lists it: its resolved nodes in the
 *  board's `ChainNode` shape (`store.node_view`), or the error that stops it
 *  resolving. */
export interface TemplateSummary {
  id: string;
  nodes: ChainNode[];
  gates: number;
  /** Absent or null when the chain resolves. */
  error?: string | null;
}

/** One chain file as its author wrote it (`GET /templates/{id}`). */
export interface ChainFile {
  id: string;
  file: string;
  text: string;
  chain: Record<string, unknown>;
}

/** A problem `GET /templates/lint` or `POST /templates/resolve` reports. */
export interface TemplateIssue {
  file: string;
  chain: string | null;
  message: string;
}

/** `POST /templates/resolve`: every chain that resolved, and every issue. */
export interface ResolveResult {
  chains: { id: string; nodes: ChainNode[] }[];
  issues: TemplateIssue[];
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
