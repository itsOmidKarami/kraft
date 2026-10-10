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
  default_chain: string;
  test_command: string | null;
  test_scopes: TestScope[] | null;
  /** How a fresh worktree is prepared; `""` for nothing to run, null for none declared. */
  setup_command?: string | null;
  forge: string | null;
  project: string | null;
  enabled: boolean;
  /** Model per harness profile id (Ruling 165), e.g. `{claude: "opus"}`. */
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

/** One command the probe's evidence supports (`kraft.detect.Candidate`). */
export interface ProbeCandidate {
  /** Relative to the repo root; `""` is the root. */
  dir: string;
  role: "test" | "setup";
  command: string;
  tier: "runner" | "ci" | "toolchain" | "devenv";
  /** What it was read from, for a person: "justfile recipe `test`". */
  source: string;
  marker: string;
  detector: string;
  family: string | null;
  corroborated: boolean;
  /** Part of the proposal, rather than an alternative. */
  chosen: boolean;
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
  /** What `repo connect` and `POST /repos` save as `setup_command` when the caller gives none. */
  setup_command: string | null;
  forge: string | null;
  project: string | null;
  /** Every command the evidence supports, chosen or not. Told, never stored. */
  candidates?: ProbeCandidate[];
  /** The commit the proposal was read from (`refs/remotes/origin/main`). */
  read_from?: string | null;
  /** Directories with tests and nothing to prepare them: `setup_command` is left undecided. */
  missing_setup?: string[];
  /** Why no test command was proposed, by directory. */
  stopped?: ProbeStop[];
  /** Programs a proposed command runs that the server's PATH does not have. */
  missing_tools?: MissingTool[];
}

/** A program a proposed command runs that the server's PATH lacks: a work
 *  item there fails on it. */
export interface MissingTool {
  dir: string;
  tool: string;
}

/** A directory the probe proposes no test command for, and why. */
export interface ProbeStop {
  dir: string;
  reason: string;
  detector: string;
}

/** One saved chain as `GET /templates/chains` lists it: its resolved nodes in the
 *  board's `ChainNode` shape (`store.node_view`), or the error that stops it
 *  resolving. */
export interface TemplateSummary {
  id: string;
  /** The plugin it comes from, read-only here; null or absent for the instance's own. */
  plugin?: Plugin | null;
  nodes: ChainNode[];
  gates: number;
  /** Absent or null when the chain resolves. */
  error?: string | null;
  /** Per node id, the library components that node is built from
   *  (`tasks.implementer`). A node that uses none is absent. */
  uses?: Record<string, string[]>;
}

/** The plugin a chain or library component is loaded from. */
export interface Plugin {
  id: string;
  version: string;
}

/** One reusable component of `library.yaml` (`GET /templates/library`). */
export interface LibraryComponent {
  /** `<section>.<name>`: `tasks.implementer`. */
  id: string;
  /** The `library.yaml` section: `tasks`, `steps`, `nodes`, `steering`. */
  kind: string;
  name: string;
  /** As its author wrote it: no defaults filled in. */
  definition: Record<string, unknown>;
  /** The chain ids that use it, directly or through another component. */
  used_by: string[];
  issues: TemplateIssue[];
  plugin?: Plugin | null;
}

/** `library.yaml`'s text, which the Library screen edits, and its components. */
export interface Library {
  file: string;
  text: string;
  components: LibraryComponent[];
}

/** One `harnesses.yaml` profile (`GET /harnesses/profiles`, Kraft-archr). */
export interface HarnessProfile {
  id: string;
  /** The provider package it configures: `claude`, `codex`, `gemini`. */
  provider: string;
  enabled: boolean;
  /** `null`: the provider's own command. */
  executable: string | null;
  defaults: Record<string, string>;
  /** The library tasks that select it, as `tasks.<name>`. */
  used_by: string[];
  /** The chains with an agent task selecting it. */
  chains: string[];
}

/** What a profile save sends (`PUT /harnesses/profiles/{id}`). */
export type HarnessProfileInput = Pick<HarnessProfile, "provider" | "enabled" | "defaults"> & {
  executable?: string;
};

/** One entry of an agent profile's `fallback:` list, as Settings shows it. */
export interface FallbackEntryView {
  harness?: string;
  profile?: string;
  model?: string;
  effort?: string;
  problems: string[];
}

/** One `profiles:` entry of `harnesses.yaml` (Kraft-ps1ao): a model tier a
 *  task selects with `profile:`. Read-only here; edited in the file. */
export interface AgentProfile {
  id: string;
  effort: string | null;
  /** Provider id -> model id. A provider it omits cannot run it. */
  model: Record<string, string>;
  /** Provider id -> the model and effort it runs there; `effort` above is
   *  null when these differ. */
  providers?: Record<string, { model?: string | null; effort?: string | null }>;
  used_by: string[];
  chains: string[];
  /** Why a task pairing it would not launch, in the launch's words. */
  problems: string[];
  /** Its default fallback list (Kraft-0a3h8), each entry with the pairing
   *  problems of the tasks that take it. Optional: an older server sends none. */
  fallback?: FallbackEntryView[];
}

export interface Harnesses {
  file: string;
  /** Why `harnesses.yaml` does not load; `profiles` is then empty. */
  error: string | null;
  profiles: HarnessProfile[];
  agent_profiles: AgentProfile[];
}

/** One capability a provider declares. `values` are `re.fullmatch` patterns;
 *  empty means any value. */
export interface HarnessCapability {
  /** The `cli:` fragment, with `{value}`/`{csv}` placeholders; empty for one
   *  that is read (`source`/`reader`) or carried `via` a command prefix. */
  cli: string[];
  values: string[];
  always: string | string[] | null;
  channel: string | null;
  source: string | null;
  reader: string | null;
  via: string | null;
  /** `permission_mode` only: the mode a launch under a tool allowlist runs in. */
  under_allowlist: string | null;
}

/** A provider package's read-only capability surface (`GET /harnesses/providers`). */
export interface HarnessProvider {
  id: string;
  kind: string;
  command: string[];
  path: string;
  /** From `$KRAFT_HOME/config/harnesses/` rather than the package. */
  override: boolean;
  capabilities: Record<string, HarnessCapability>;
}

export interface HarnessProviders {
  valid: Record<string, HarnessProvider>;
  /** Provider id -> why its file did not load. */
  invalid: Record<string, string>;
}

/** One chain file as its author wrote it (`GET /templates/chains/{id}`). */
export interface ChainFile {
  id: string;
  file: string;
  text: string;
  chain: Record<string, unknown>;
  plugin?: Plugin | null;
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

/** `GET/PUT /theme`. Instance-wide, like every other Settings-backed value. */
export type BoardGroupBy = "status" | "repo" | "chain";
export type BoardOpenIn = "peek" | "full";

export interface Theme {
  /** Legacy, accepted on PUT only: GET answers without it, and startup
   *  converts a file's palette to the three keys below (W18). */
  palette?: PaletteId;
  mode: ThemeMode;
  density: "compact" | "comfortable";
  board: { group_by: BoardGroupBy; show_done: number; open_in: BoardOpenIn };
  // The colour model. `GET` always fills the three; `derived` says the file
  // names no surface, so they are the default look (or an old `palette`'s).
  surface?: Surface;
  accent?: Accent;
  colour_amount?: ColourAmount;
  derived?: boolean;
  code_scheme?: {
    light: "auto" | "none" | "solarized-light";
    dark: "auto" | "none" | "solarized-dark" | "monokai" | "dracula";
  };
  diff?: {
    layout: "unified" | "split";
    colours: "theme" | "safe" | "plain";
    show_whitespace: boolean;
    word_highlight: boolean;
    wrap_lines: boolean;
    one_file_at_a_time: boolean;
  };
  /** The document viewer's Open in editor (`GET /editors`); unset is the system's default app. */
  editor?: string | null;
}

export type Surface = "graphite" | "slate" | "ink" | "sand" | "moss";
export type Accent = "none" | "blue" | "violet" | "green" | "amber" | "rose";
export type ColourAmount = "mono" | "subtle" | "full";

export interface Access {
  bind: string;
  port: number;
  session_expiry_days: number;
  password_set: boolean;
  auth_required: boolean;
  allowed_hosts: string[];
  /** This machine's LAN address and host name, offered for `allowed_hosts` when switching to a network bind. */
  lan_hosts?: string[];
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
