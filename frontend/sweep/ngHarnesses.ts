/** The Harnesses page's server (ux2-W14): the `harnesses` draft's answers, shaped like
 *  `src/kraft/drafts/harnesses.py`'s resolve, with the few ops the flows send applied to a
 *  small in-memory state so problems appear and clear as the real draft's do. */

type Access = "available" | "override" | "never";
interface Task { chain: string; path: string; profile: string | null; fallback: boolean }
const t = (path: string, profile: string | null): Task => ({ chain: "default", path, profile, fallback: false });

const HARNESSES: { id: string; provider: string; defaults: Record<string, string>; executable?: string; found: boolean; tasks: Task[] }[] = [
  { id: "claude", provider: "claude", defaults: { model: "sonnet", permission_mode: "acceptEdits" }, found: true, tasks: [t("implementation.main.implementer", "strong"), t("verification.fix.repair_verification", "strong"), t("verification.fix.strict_judge", "strong"), t("spec.main.spec_author", null), t("plan.main.plan_author", null), t("review.main.code_review", null), t("finish.main.describe_mr", null)] },
  { id: "claude-sandbox", provider: "claude", defaults: { model: "sonnet" }, executable: "claude --sandbox", found: true, tasks: [] },
  { id: "codex", provider: "codex", defaults: { effort: "medium" }, found: true, tasks: [t("finish.main.write_summary", "fast")] },
  { id: "cursor", provider: "cursor", defaults: {}, found: true, tasks: [] },
  { id: "gemini", provider: "gemini", defaults: {}, found: false, tasks: [] },
  { id: "opencode", provider: "opencode", defaults: {}, found: true, tasks: [] },
  { id: "amp", provider: "amp", defaults: {}, found: true, tasks: [] },
];

export const PROVIDER_STATUS = [
  { id: "claude", label: "claude", executable: "claude", executable_found: true, efforts: ["low", "medium", "high", "xhigh", "max"], models: ["sonnet", "opus", "haiku"], capabilities: { effort: { cli: ["--effort", "{value}"], values: [] }, model: { cli: ["--model", "{value}"], values: [] } } },
  { id: "codex", label: "codex", executable: "codex", executable_found: true, efforts: ["minimal", "low", "medium", "high", "xhigh"], models: [], capabilities: { effort: { cli: ["-c", "model_reasoning_effort={value}"], values: [] } } },
  { id: "cursor", label: "cursor", executable: "cursor-agent", executable_found: true, efforts: [], models: [], capabilities: {} },
  { id: "gemini", label: "gemini", executable: "gemini", executable_found: false, efforts: [], models: [], capabilities: {} },
  { id: "opencode", label: "opencode", executable: "opencode", executable_found: true, efforts: [], models: [], capabilities: {} },
  { id: "amp", label: "amp", executable: "amp", executable_found: true, efforts: ["low", "medium", "high", "ultra"], models: [], capabilities: {} },
];

export type Scenario = "floor" | "problems" | "empty";

interface State {
  access: Record<string, Access>;
  tools: string[];
  grants: string[];
  escalation: string | null;
  profiles: Record<string, Record<string, { model: string; effort?: string }>>;
  extra: Task[];
  /** Each harness's own fields (`set_harness`). */
  fields: Record<string, { enabled: boolean; executable: string | null; defaults: Record<string, string> }>;
}

const initial = (scenario: Scenario): State => {
  const base: State = {
    access: { claude: "available", "claude-sandbox": "override", codex: "available", cursor: "never", gemini: "never", opencode: "never", amp: "never" },
    tools: ["git", "shell", "editor"],
    grants: ["git-commit", "git-rebase", "git-push"],
    escalation: "claude",
    profiles: {
      strong: { claude: { model: "sonnet", effort: "high" }, codex: { model: "gpt-5.6-terra", effort: "high" } },
      fast: { claude: { model: "haiku", effort: "low" }, codex: { model: "gpt-5.6-mini", effort: "low" } },
      deep: { claude: { model: "opus", effort: "high" }, codex: { model: "gpt-5.6-sol", effort: "high" } },
    },
    extra: [],
    fields: Object.fromEntries(HARNESSES.map((h) => [h.id, { enabled: true, executable: h.executable ?? null, defaults: { ...h.defaults } }])),
  };
  if (scenario === "problems") {
    base.profiles.fast = { claude: { model: "haiku", effort: "low" } };
    base.escalation = "cursor";
    base.extra = [t("review.bot.second_opinion", null)];
  }
  if (scenario === "empty") return { ...base, profiles: {}, access: Object.fromEntries(Object.keys(base.access).map((k) => [k, "available"])) as Record<string, Access>, escalation: "item" };
  return base;
};

export function harnessesServer(scenario: Scenario) {
  const state = initial(scenario);
  // The `problems` scenario opens with a draft already holding the change that causes them.
  if (scenario === "problems") state.access.gemini = "never";
  // The published state the draft is measured against: the problems scenario is a draft that broke a clean install.
  const original = initial(scenario === "problems" ? "floor" : scenario);
  const tasksOf = (id: string): Task[] => [...HARNESSES.find((h) => h.id === id)!.tasks, ...(id === "gemini" ? state.extra : [])];

  const problems = () => {
    const out: Record<string, unknown>[] = [];
    for (const h of HARNESSES) {
      for (const task of tasksOf(h.id)) {
        if (state.access[h.id] === "never") out.push({ path: task.path, chain: task.chain, field: "allowed_harnesses", message: `${task.path}: harness '${h.id}' is not in its allowed_harnesses ['claude', 'codex']`, file: "chains/default.yaml", line: 1, col: 1 });
        const prof = task.profile && state.profiles[task.profile];
        if (task.profile && prof && !prof[h.provider]) out.push({ path: task.path, chain: task.chain, field: "profile", message: `${task.path}: profile '${task.profile}' has no ${h.provider} entry`, file: "harnesses.yaml", line: 1, col: 1, profile: task.profile, provider: h.provider });
      }
    }
    const esc = state.escalation ?? "claude";
    if (esc !== "item" && state.access[esc] === "never") out.push({ path: "defaults.escalation_harness", field: "escalation_harness", message: `escalation runs on '${esc}', which is set to Never`, file: "policy.yaml", line: null, col: null, fix: "Pick another harness, or set this one to Available or Override." });
    return out;
  };

  const changes = () => {
    const out: { path: string; kind: string; summary: string; fields: string[] }[] = [];
    for (const [h, a] of Object.entries(state.access)) if (a !== original.access[h]) out.push({ path: `access.${h}`, kind: "change", summary: `${original.access[h]} -> ${a}`, fields: ["allowed_harnesses"] });
    for (const [name, p] of Object.entries(state.profiles)) if (JSON.stringify(p) !== JSON.stringify(original.profiles[name])) out.push({ path: `profiles.${name}`, kind: original.profiles[name] ? "change" : "add", summary: "providers", fields: ["providers"] });
    for (const [h, f] of Object.entries(state.fields)) if (JSON.stringify(f) !== JSON.stringify(original.fields[h])) out.push({ path: `harnesses.${h}`, kind: "change", summary: Object.keys(f).filter((k) => JSON.stringify((f as any)[k]) !== JSON.stringify((original.fields[h] as any)[k])).join(", "), fields: Object.keys(f).filter((k) => JSON.stringify((f as any)[k]) !== JSON.stringify((original.fields[h] as any)[k])).sort() });
    if (JSON.stringify(state.tools) !== JSON.stringify(original.tools)) out.push({ path: "maxima.allowed_tools", kind: "change", summary: "allowed_tools", fields: ["allowed_tools"] });
    if (JSON.stringify(state.grants) !== JSON.stringify(original.grants) || state.escalation !== original.escalation) out.push({ path: "defaults.escalation", kind: "change", summary: "escalation", fields: ["escalation_harness"] });
    return out;
  };

  const resolved = () => ({
    harnesses: HARNESSES.map((h) => ({
      id: h.id, state: state.access[h.id], executable_found: h.found, provider: h.provider, ...state.fields[h.id],
      tasks: tasksOf(h.id).map((task) => ({ ...task })),
    })),
    allowed_tools: state.tools,
    escalation: { harness: state.escalation, grants: state.grants },
    escalation_effective: { harness: state.escalation ?? "claude", set: state.escalation !== null },
    profiles: Object.fromEntries(Object.entries(state.profiles).map(([name, providers]) => [name, {
      providers, effort: null, used_by_fallback: [],
      tasks: HARNESSES.flatMap((h) => tasksOf(h.id).filter((task) => task.profile === name)).map((task) => ({ chain: task.chain, path: task.path })),
    }])),
  });

  let clean = false;
  /** Publish: what is published becomes the draft, so the next view has no changes. */
  const publish = () => { Object.assign(original, structuredClone(state)); clean = true; };

  const view = () => {
    const c = changes();
    return {
      area: "harnesses", key: "harnesses", draft: c.length > 0 || (scenario === "problems" && !clean),
      files: { "harnesses.yaml": harnessesYaml(state), "policy.yaml": policyYaml(state) },
      published: { "harnesses.yaml": harnessesYaml(original), "policy.yaml": policyYaml(original) },
      base: { "harnesses.yaml": "a".repeat(64), "policy.yaml": "b".repeat(64) }, updated_at: "2026-10-01T09:12:00Z",
      result: { model: {}, resolved: resolved(), problems: problems(), sources: {}, changes: c, impact: { tasks: 3, chains: ["default"] }, warnings: [], policy_values: { auto_escalate_delay_s: 0, auto_review_attempts: 1 } },
    };
  };

  const apply = (ops: Record<string, any>[]) => {
    for (const o of ops) {
      if (o.op === "set_access") state.access[o.harness] = o.state;
      else if (o.op === "set_allowed_tools") state.tools = o.tools ?? [];
      else if (o.op === "set_escalation") { state.escalation = o.harness; state.grants = o.grants ?? []; }
      else if (o.op === "set_profile") for (const [prov, e] of Object.entries<any>(o.patch.providers ?? {})) { if (e === null) delete state.profiles[o.name][prov]; else state.profiles[o.name][prov] = e; }
      else if (o.op === "set_harness") {
        const f = state.fields[o.id], p = o.patch;
        if ("enabled" in p) f.enabled = p.enabled;
        if ("executable" in p) f.executable = p.executable;
        for (const [k, v] of Object.entries<any>(p.defaults ?? {})) { if (v === null) delete f.defaults[k]; else f.defaults[k] = v; }
      }
      else if (o.op === "add_profile") state.profiles[o.name] = o.copy_from ? JSON.parse(JSON.stringify(state.profiles[o.copy_from])) : {};
    }
  };
  return { view, apply, publish, problems, count: () => Object.keys(state.profiles).length };
}

const harnessesYaml = (s: State) => `harnesses:\n${HARNESSES.map((h) => `  ${h.id}:\n    provider: ${h.provider}\n`).join("")}profiles:\n${Object.entries(s.profiles).map(([n, p]) => `  ${n}:\n    providers:\n${Object.entries(p).map(([pv, e]) => `      ${pv}: {model: ${e.model}${e.effort ? `, effort: ${e.effort}` : ""}}\n`).join("")}`).join("")}`;
const policyYaml = (s: State) => `defaults:\n  allowed_harnesses: [${Object.keys(s.access).filter((h) => s.access[h] === "available").join(", ")}]\n  escalation_harness: ${s.escalation}\n  escalation_grants: [${s.grants.join(", ")}]\nmaxima:\n  allowed_tools: [${s.tools.join(", ")}]\n  allowed_harnesses: [${Object.keys(s.access).filter((h) => s.access[h] !== "never").join(", ")}]\n`;
