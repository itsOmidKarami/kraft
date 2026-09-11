/* ── settings (design 5a–5e) ─────────────────────────────────────────────── */

export interface Repo {
  path: string;
  name: string;
  default_chain_template: string;
  test_command: string | null;
  forge: string | null;
  project: string | null;
  enabled: boolean;
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
  forge: string | null;
  project: string | null;
}

export interface TemplateNode {
  id: string;
  tasks: string[];
  gate_after: string | null;
  fix_loop?: string | null;
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
  kind: "builtin" | "agent" | "subprocess";
  handler?: string;
  command?: string | string[];
  interactive?: boolean;
  repos?: Record<string, boolean>;
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
  max_concurrent: number;
  priority_ceiling: number;
  repos: string[];
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
}

export type PaletteId = "nocturne" | "rose" | "forest" | "amber" | "slate";
export type ThemeMode = "light" | "dark" | "system";

/** `GET/PUT /theme`. Instance-wide, like every other Settings-backed value —
 *  see the theme-palettes design doc for why this isn't per-user. */
export interface Theme {
  palette: PaletteId;
  mode: ThemeMode;
}

export interface Access {
  bind: string;
  port: number;
  session_expiry_days: number;
  password_set: boolean;
  auth_required: boolean;
}

/** `GET /notify`. The webhook URL is deliberately absent — the server never
 *  sends it back, so there is nothing here to accidentally render. */
export interface Notify {
  enabled: boolean;
  url_set: boolean;
  base_url: string | null;
  events: string[];
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
