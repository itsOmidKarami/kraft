import { command, dollars, list, models, scopes, text, whole, type Parsed } from "./format";
import type { RepoView } from "./types";

/** The Config rows of a repo (GAP §2 #33: every field the shipped page edits),
 *  in the order the pane draws them. `policy.*` rows edit a key of the entry's
 *  `policy:` block; the rest are keys of the entry. */
export interface RepoField {
  key: string;
  label: string;
  /** A fixed choice (a chain, a forge) instead of typed text. */
  choice?: "chain" | "forge";
  show: (v: unknown) => string;
  parse: (t: string) => Parsed;
  /** Where its source word comes from, when `resolved.sources` has one. */
  source?: "steering" | "deny_tools" | "models";
  /** The value shown when the entry sets none. */
  fallback?: string;
  /** A checkbox that sets the field to `""`, the command that does nothing. */
  none?: string;
  placeholder?: string;
}

const policy = (key: string, label: string, p: Pick<RepoField, "show" | "parse">): RepoField => ({ key: `policy.${key}`, label, ...p, placeholder: "not set" });

export const FIELDS: RepoField[] = [
  { key: "default_chain_template", label: "default chain", choice: "chain", ...text, fallback: "default" },
  { key: "test_command", label: "test command", ...text },
  { key: "test_scopes", label: "test scopes", ...scopes, placeholder: "paths => command; …" },
  { key: "setup_command", label: "setup command", ...command, none: "No setup needed" },
  { key: "intent_dir", label: "intent dir", ...text },
  { key: "steering", label: "steering", ...list, source: "steering", placeholder: "profile, profile" },
  { key: "models", label: "models", ...models, source: "models", placeholder: "profile=model; …" },
  { key: "deny_tools", label: "deny tools", ...list, source: "deny_tools", placeholder: "tool, tool" },
  { key: "local_files", label: "local files", ...list, placeholder: "file, file" },
  { key: "forge", label: "forge", choice: "forge", ...text },
  { key: "project", label: "project", ...text },
  policy("time_cap_minutes", "tasks running cap (min)", whole("Minutes")),
  policy("total_time_cap_minutes", "tasks wall clock cap (min)", whole("Minutes")),
  policy("token_budget", "tokens cap", whole("Tokens")),
  policy("budget_usd", "dollars cap", dollars),
  { ...policy("allowed_tools", "allowed tools", list), placeholder: "tool, tool" },
  { ...policy("allowed_harnesses", "allowed harnesses", list), placeholder: "harness, harness" },
];

export const FORGES = ["gitlab", "github", "none"];

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
