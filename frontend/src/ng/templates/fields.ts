/** How the Config tab edits each field (W10 brief Decided 11). Rows and values
 *  come from the server's `sources`; this says only the label and the editor.
 *  Labels are the Templates prototype's (`kraft-templates-model.js` FIELDS). */
export type FieldKind = "bool" | "enum" | "int" | "minutes" | "number" | "duration" | "list" | "long" | "text";
/** `restart`: this and the earlier exec nodes, for `on_base_changed.restart_from` (Decisions §9 Node settings). */
export type FieldMeta = { label: string; kind: FieldKind; options?: string[] | "harness" | "effort" | "profile" | "restart"; choices?: ChoiceSource; suggest?: "model" };
/** Where a typed field's closed set comes from: the draft's `choices` (`ref`,
 *  `target`, `inputs`, `grants`), the library's steering profiles, the
 *  harnesses, the documents a gate can ask about, or the nodes before it. */
export type ChoiceSource = "ref" | "target" | "inputs" | "grants" | "steering" | "harnesses" | "documents" | "earlier";

const META: Record<string, FieldMeta> = {
  harness: { label: "harness", kind: "enum", options: "harness" },
  prompt: { label: "prompt", kind: "long" },
  skill: { label: "skill", kind: "text" },
  produces: { label: "produces", kind: "text" },
  profile: { label: "profile", kind: "enum", options: "profile" },
  model: { label: "model", kind: "text", suggest: "model" },
  effort: { label: "effort", kind: "enum", options: "effort" },
  inputs: { label: "inputs", kind: "list", choices: "inputs" },
  fallback: { label: "fallback", kind: "list" },
  steering: { label: "steering", kind: "list", choices: "steering" },
  scope: { label: "runs", kind: "enum", options: ["once", "each_repository"] },
  execution: { label: "order", kind: "enum", options: ["sequential", "parallel"] },
  skippable: { label: "skippable", kind: "bool" },
  read_only: { label: "read only", kind: "bool" },
  ref: { label: "action", kind: "text", choices: "ref" },
  command: { label: "command", kind: "text" },
  target: { label: "target", kind: "text", choices: "target" },
  message: { label: "message", kind: "long" },
  artifact: { label: "document", kind: "text", choices: "documents" },
  artifact_required: { label: "document required", kind: "bool" },
  reject_to: { label: "reject to", kind: "text", choices: "earlier" },
  timeout: { label: "timeout", kind: "duration" },
  chain_finalized: { label: "final review", kind: "bool" },
  max_attempts: { label: "attempts", kind: "int" },
  on_base_changed: { label: "on base change", kind: "enum", options: "restart" },
  "wait.polling.initial_interval": { label: "check every", kind: "duration" },
  "wait.polling.max_interval": { label: "backs off to", kind: "duration" },
  "policy.time_cap_minutes": { label: "running cap", kind: "minutes" },
  "policy.total_time_cap_minutes": { label: "total cap", kind: "minutes" },
  "policy.timeout_minutes": { label: "wall clock", kind: "minutes" },
  "policy.max_attempts": { label: "attempts", kind: "int" },
  "policy.token_budget": { label: "token budget", kind: "int" },
  "policy.budget_usd": { label: "budget ($)", kind: "number" },
  "policy.allowed_harnesses": { label: "allowed harnesses", kind: "list", choices: "harnesses" },
  "policy.allowed_tools": { label: "allowed tools", kind: "list" },
  "policy.deny_tools": { label: "denied tools", kind: "list" },
  "policy.grants": { label: "grants", kind: "list", choices: "grants" },
};

/** An unlisted field edits as text under its own name (`policy.sandbox` → "sandbox"). */
export const fieldMeta = (field: string): FieldMeta => META[field] ?? { label: field.replace(/^policy\./, "").replace(/_/g, " "), kind: "text" };

/** A value as a row shows it. */
export function show(value: unknown, kind: FieldKind): string {
  if (value === null || value === undefined || value === "") return "not set";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (Array.isArray(value)) return value.length ? value.map((v) => (typeof v === "string" ? v : JSON.stringify(v))).join(", ") : "none";
  if (typeof value === "object") {
    const from = (value as { restart_from?: unknown }).restart_from;
    return typeof from === "string" ? `restart from ${from}` : JSON.stringify(value);
  }
  return kind === "minutes" ? `${value}m` : String(value);
}

/** The typed text, as the value set_field sends; an error string when it can't be one.
 *  The server checks again; this is only the shape (Decided 11). */
export function parse(text: string, kind: FieldKind): { value: unknown } | { error: string } {
  const t = text.trim();
  if (t === "") return { value: null };
  if (kind === "int" || kind === "minutes") return /^\d+$/.test(t) && Number(t) > 0 ? { value: Number(t) } : { error: "A whole number above 0." };
  if (kind === "number") return Number.isFinite(Number(t)) && Number(t) >= 0 ? { value: Number(t) } : { error: "A number." };
  if (kind === "duration") return /^[1-9]\d*[smhd]$/.test(t) ? { value: t } : { error: "Like 30s, 5m, 2h or 1d." };
  if (kind === "list") return { value: t.split(",").map((x) => x.trim()).filter(Boolean) };
  return { value: kind === "long" ? text : t };
}
