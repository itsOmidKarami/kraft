import { command, dollars, list, models, onByDefault, scopes, text, whole, type Parsed } from "./format";
import type { RepoView } from "./types";

/** The Config rows of a repo (GAP §2 #33: every field the shipped page edits),
 *  in the order the pane draws them. `policy.*` rows edit a key of the entry's
 *  `policy:` block; the rest are keys of the entry. */
export interface RepoField {
  key: string;
  group: (typeof GROUPS)[number];
  label: string;
  /** A fixed choice (a chain, a forge, on or off) instead of typed text. */
  choice?: "chain" | "forge" | "bool";
  show: (v: unknown) => string;
  parse: (t: string) => Parsed;
  /** Where its source word comes from, when `resolved.sources` has one. */
  source?: "steering" | "deny_tools" | "models";
  /** The value shown when the entry sets none. */
  fallback?: string;
  /** A closed set its comma-separated entries come from, listed as you type. */
  choices?: "steering" | "harnesses";
  /** A checkbox that sets the field to `""`, the command that does nothing. */
  none?: string;
  /** What the cell says for that `""`, when the quote marks alone would not say it. */
  empty?: string;
  placeholder?: string;
}

/** The section heads of the Config tab, in order. */
export const GROUPS = ["Running", "Tests", "Agents", "Limits", "Forge"] as const;

/** `test_command: ""`: a repo with no tests, whose items pass verify without
 *  running any. Not "not set", which stops every item at verify. */
export const NO_TESTS = "no tests (passes verify)";

const policy = (key: string, label: string, p: Pick<RepoField, "show" | "parse">, group: RepoField["group"] = "Limits"): RepoField => ({ key: `policy.${key}`, group, label, ...p, placeholder: "not set" });

export const FIELDS: RepoField[] = [
  { key: "default_chain", group: "Running", label: "default chain", choice: "chain", ...text, fallback: "default" },
  { key: "test_command", group: "Tests", label: "test command", ...command, none: "No tests", empty: NO_TESTS },
  { key: "test_scopes", group: "Tests", label: "test scopes", ...scopes, placeholder: "paths => command; …" },
  { key: "setup_command", group: "Running", label: "setup command", ...command, none: "No setup needed", empty: "none" },
  { key: "intent_dir", group: "Running", label: "intent dir", ...text },
  { key: "worktrees", group: "Running", label: "worktrees", choice: "bool", ...onByDefault("Each item in its own worktree", "Items run in this repo's checkout") },
  { key: "steering", group: "Agents", label: "steering", ...list, source: "steering", choices: "steering", placeholder: "profile, profile" },
  { key: "models", group: "Agents", label: "models", ...models, source: "models", placeholder: "profile=model; …" },
  { key: "deny_tools", group: "Agents", label: "deny tools", ...list, source: "deny_tools", placeholder: "tool, tool" },
  { key: "local_files", group: "Running", label: "local files", ...list, placeholder: "file, file" },
  { key: "forge", group: "Forge", label: "forge", choice: "forge", ...text },
  { key: "project", group: "Forge", label: "project", ...text },
  policy("time_cap_minutes", "tasks running cap (min)", whole("Minutes")),
  policy("total_time_cap_minutes", "tasks wall clock cap (min)", whole("Minutes")),
  policy("token_budget", "tokens cap", whole("Tokens")),
  policy("budget_usd", "dollars cap", dollars),
  { ...policy("allowed_tools", "allowed tools", list, "Agents"), placeholder: "tool, tool" },
  { ...policy("allowed_harnesses", "allowed harnesses", list, "Agents"), choices: "harnesses", placeholder: "harness, harness" },
];

/** Keys of an entry the Config tab has no row for; they are edited in the repo's YAML. */
export const YAML_ONLY: { key: string; label: string }[] = [
  { key: "env", label: "env" },
  { key: "sandbox", label: "sandbox" },
  { key: "ci_checks", label: "CI checks" },
  { key: "library_auto_update", label: "library auto-update" },
  { key: "automated_review", label: "automated review" },
];

export const FORGES = ["gitlab", "github", "none"];

/** The forge choices, with a value outside the list (the test double `fake`) kept so the control shows what the entry says. */
export const forgeOptions = (v: unknown): string[] => (typeof v === "string" && v && !FORGES.includes(v) ? [v, ...FORGES] : FORGES);

/** The entry's value at a field's key (`policy.x` reads inside `policy:`). */
export function valueOf(r: RepoView, f: RepoField): unknown {
  if (f.key.startsWith("policy.")) return (r.entry.policy as Record<string, unknown> | null | undefined)?.[f.key.slice(7)];
  return r.entry[f.key];
}

/** The `set_repo` patch that sets (or, with null, clears) a field. */
export function patchFor(r: RepoView, f: RepoField, value: unknown): Record<string, unknown> {
  if (!f.key.startsWith("policy.")) return { [f.key]: value };
  const block = { ...((r.entry.policy as Record<string, unknown> | null | undefined) ?? {}) };
  if (value === null) delete block[f.key.slice(7)];
  else block[f.key.slice(7)] = value;
  return { policy: Object.keys(block).length ? block : null };
}

/** "this repo", "default" or "library" for a row. */
export function sourceOf(r: RepoView, f: RepoField): string {
  const s = f.source ? r.sources[f.source] : f.key.startsWith("policy.") ? r.sources.policy[f.key.slice(7)] : undefined;
  const set = valueOf(r, f) != null && !(Array.isArray(valueOf(r, f)) && (valueOf(r, f) as unknown[]).length === 0);
  const word = s ?? (set ? "repo" : "default");
  return word === "repo" ? "this repo" : word;
}
