/**
 * ux2-W15: the `repos`, `policy` and `intake` config drafts, answered the way
 * W13's server does (docsite "Drafts", "Repos ops", "Policy ops", "Intake ops",
 * and W15 A). One instance per page: ops change it, `undo` pops the last,
 * `preview=1` answers without saving, a publish folds the draft into the
 * published state.
 * The product reads none of this: it is the suite's stand-in for a server.
 */

type Obj = Record<string, any>;
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

/* ── a small YAML writer, for the files' text ─────────────────────────────── */

const NEEDS_QUOTES = /^[\s\-?:,[\]{}#&*!|>'"%@`]|: | #|^$|^(true|false|null|\d.*)$/i;
const scalar = (v: unknown) => (typeof v === "string" ? (NEEDS_QUOTES.test(v) ? JSON.stringify(v) : v) : String(v));
export function yamlOf(v: unknown, pad = ""): string {
  if (Array.isArray(v)) {
    if (!v.length) return pad + "[]\n";
    if (v.every((x) => x === null || typeof x !== "object")) return pad + `[${v.map(scalar).join(", ")}]\n`;
    return v.map((x) => {
      const body = yamlOf(x, pad + "  ").replace(new RegExp(`^${pad}  `), "");
      return `${pad}- ${body}`;
    }).join("");
  }
  if (v && typeof v === "object") {
    return Object.entries(v as Obj).filter(([, x]) => x !== undefined).map(([k, x]) => {
      if (x && typeof x === "object" && !(Array.isArray(x) && x.every((y) => y === null || typeof y !== "object")) && !(Array.isArray(x) && !x.length) && Object.keys(x).length)
        return `${pad}${k}:\n${yamlOf(x, pad + "  ")}`;
      const flat = yamlOf(x, "").trimEnd();
      return `${pad}${k}: ${Array.isArray(x) ? flat : x && typeof x === "object" ? "{}" : scalar(x)}\n`;
    }).join("");
  }
  return pad + scalar(v) + "\n";
}

/* ── the three states ─────────────────────────────────────────────────────── */

const TOOL_CEILING = ["Read", "Grep"];
const RUNNING: Record<string, number> = { "/Users/me/src/platform": 1 };

const repoSeed = (): Obj[] => [
  { path: "/Users/me/src/product_root", name: "product_root", default_chain: "default", steering: ["project-standards"], test_command: "pytest -q", setup_command: "uv sync", intent_dir: "docs/intent", models: { claude: "opus" }, forge: "gitlab", project: "acme/product_root", enabled: true, managed: true },
  { path: "/Users/me/src/platform", name: "platform", default_chain: "default", steering: ["never-signal-processes-you-didnt-start"], test_command: "make test", setup_command: "uv sync", forge: "gitlab", project: "acme/platform", policy: { time_cap_minutes: 60 }, enabled: true, managed: true },
  { path: "/Users/me/src/docs-site", name: "docs-site", default_chain: "docs_only", test_command: "npm test", setup_command: "npm ci", forge: "github", project: "acme/docs-site", enabled: false, managed: true },
  { path: "/Users/me/src/product_root/plugins", name: "plugins", managed: false },
];

const policySeed = (): Obj => ({
  budget: { work_item_usd: 10, daily_usd: 50 },
  defaults: { max_attempts: 3, timeout_minutes: 60, work_item: { time_cap_minutes: 480, total_time_cap_minutes: 2880, budget_usd: 20 }, nodes: { time_cap_minutes: 180 }, steps: { time_cap_minutes: 120 }, tasks: { time_cap_minutes: 90 } },
  maxima: { timeout_minutes: 180, work_item: { time_cap_minutes: 1440, budget_usd: 25, token_budget: 2000000 }, tasks: { time_cap_minutes: 240, total_time_cap_minutes: 10080 } },
  default: { attempts: 3, wall_clock_s: 3600 },
  loops: { "verification.fix_loop": { attempts: 2, wall_clock_s: 3600 }, "merge_request_feedback.fix_loop": { attempts: 3, wall_clock_s: 3600 } },
  findings: { loop_severities: ["critical", "important"] },
  auto_escalate_stuck: true, auto_escalate_stuck_cap: 3, auto_escalate_delay_s: 0,
  rate_limit_retries: 5, forge_cli_timeout_s: 120, max_concurrent: 5, archive: { after_days: 30 },
  triggers: [{ cron: "0 9 * * 1-5", repo: "/Users/me/src/platform", chain: "default", title: "Dependency check", description: "Update pinned dependencies and run the suite." }],
});

const intakeSeed = (): Obj => ({ enabled: true, interval_s: 300, priority_ceiling: 2, repos: [] });

/** Below policy: what the library's chains set under the cap levels (A.4). */
const BELOW: Record<string, Obj[]> = {
  "tasks|time_cap_minutes": [{ layer: "library", chain: "default", path: "implementation.main.implement", via: "library:tasks.implementer", value: 120 }],
  "steps|time_cap_minutes": [{ layer: "chain", chain: "default", path: "verification.main", via: null, value: 100 }],
};
const LEVELS = ["work_item", "nodes", "steps", "tasks"];
const CAPS = ["time_cap_minutes", "total_time_cap_minutes", "budget_usd", "token_budget"];

const get = (o: Obj, path: string) => path.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
function put(o: Obj, path: string, v: unknown) {
  const ks = path.split("."); const last = ks.pop()!;
  let t = o;
  for (const k of ks) { if (v === null && t[k] == null) return; t = t[k] ??= {}; }
  if (v === null) delete t[last]; else t[last] = v;
  // prune sections the removal leaves empty
  for (let i = ks.length; i > 0; i--) {
    const parent = get(o, ks.slice(0, i - 1).join(".")) ?? o;
    const node = parent[ks[i - 1]];
    if (node && typeof node === "object" && !Array.isArray(node) && !Object.keys(node).length) delete parent[ks[i - 1]];
  }
}

function leaves(v: any, pre = ""): Record<string, unknown> {
  if (v && typeof v === "object" && !Array.isArray(v)) return Object.entries(v).reduce((a, [k, x]) => ({ ...a, ...leaves(x, pre ? `${pre}.${k}` : k) }), {});
  if (Array.isArray(v) && v.length && v.every((x) => x && typeof x === "object")) return v.reduce((a, x, i) => ({ ...a, ...leaves(x, `${pre}.${i}`) }), {});
  return { [pre]: v };
}
const shown = (p: string, v: unknown) => (v == null ? (p.startsWith("maxima.") ? "no bound" : "not set") : Array.isArray(v) ? v.join(", ") : String(v));
function keyChanges(file: string, was: Obj, now: Obj) {
  const a = leaves(was), b = leaves(now), out: Obj[] = [];
  for (const p of [...new Set([...Object.keys(a), ...Object.keys(b)])].sort()) {
    if (p in a && p in b && JSON.stringify(a[p]) === JSON.stringify(b[p])) continue;
    out.push({ path: p, kind: !(p in a) ? "add" : !(p in b) ? "remove" : "change", summary: `${shown(p, a[p])} → ${shown(p, b[p])}`, file });
  }
  return out;
}

export function makeAreas() {
  const pub = { repos: repoSeed(), policy: policySeed(), intake: intakeSeed() } as Obj;
  let draft: Record<string, Obj | null> = { repos: null, policy: null, intake: null };
  const undo: Record<string, string[]> = { repos: [], policy: [], intake: [] };

  /* files */
  const filesOf = (area: string, s: any, pol?: Obj): Record<string, string> =>
    area === "repos" ? { "repos.yaml": yamlOf({ repos: s.filter((r: Obj) => r.managed || r.managed === undefined) }) }
    : area === "policy" ? { "policy.yaml": yamlOf(s) }
    : { "intake.yaml": yamlOf(s), "policy.yaml": yamlOf(pol ?? pub.policy) };

  /* the draft of intake spans policy.yaml too: its policy half is `draftPolicy` */
  let draftPolicyForIntake: Obj | null = null;

  /* results */
  function repoResult(s: Obj[]) {
    const problems: Obj[] = [];
    const views = s.map((e) => {
      const set = new Set(Object.keys(e.policy ?? {}));
      const wide = (e.policy?.allowed_tools ?? []).filter((t: string) => !TOOL_CEILING.includes(t));
      if (wide.length) problems.push({ path: "allowed_tools", field: "allowed_tools", message: `'allowed_tools' cannot widen the inherited safety ceiling ${JSON.stringify(TOOL_CEILING).replace(/"/g, "'")}; [${wide.map((t: string) => `'${t}'`).join(", ")}] is not allowed`, file: "repos.yaml", line: 1, col: 1, repo: e.path });
      const entry = Object.fromEntries(Object.entries(e).filter(([k]) => !["managed"].includes(k)));
      return {
        path: e.path, name: e.name ?? null, managed: e.managed !== false, entry,
        resolved: { steering: e.steering ?? ["default-steering"], deny_tools: e.deny_tools ?? [], models: e.models ?? {}, policy: e.policy ?? {} },
        sources: { steering: e.steering ? "repo" : "library", deny_tools: e.deny_tools ? "repo" : "default", models: e.models ? "repo" : "default", policy: Object.fromEntries(["time_cap_minutes", "total_time_cap_minutes", "token_budget", "budget_usd"].map((k) => [k, set.has(k) ? "repo" : "default"])) },
      };
    });
    const was = pub.repos as Obj[], gone = was.filter((p) => !s.some((x) => x.path === p.path));
    for (const g of gone) if (RUNNING[g.path]) problems.push({ path: null, field: null, message: `${g.path} has ${RUNNING[g.path]} running item(s); finish them first`, file: "repos.yaml", line: 1, col: 1, repo: g.path });
    const changes: Obj[] = [];
    for (const e of s) {
      const p = was.find((x) => x.path === e.path);
      if (!p) changes.push({ path: e.path, kind: "add", summary: "", fields: Object.keys(e).sort() });
      else {
        const fields = [...new Set([...Object.keys(p), ...Object.keys(e)])].filter((k) => JSON.stringify(p[k]) !== JSON.stringify(e[k])).sort();
        if (fields.length) changes.push({ path: e.path, kind: "change", summary: fields.join(", "), fields });
      }
    }
    for (const g of gone) changes.push({ path: g.path, kind: "remove", summary: "", fields: [] });
    return {
      model: { "repos.yaml": { repos: s } }, resolved: { repos: views.filter((v) => v.managed), detected: views.filter((v) => !v.managed) },
      problems, sources: {}, changes, impact: { running: Object.fromEntries([...s, ...gone].map((e) => [e.path, RUNNING[e.path] ?? 0])) },
      warnings: [], policy_values: { auto_escalate_delay_s: 0, auto_review_attempts: 1 },
    };
  }

  function boundOf(p: Obj, level: string, cap: string) {
    for (let i = LEVELS.indexOf(level); i >= 0; i--) {
      const v = p.maxima?.[LEVELS[i]]?.[cap];
      if (v != null) return { value: v, level: LEVELS[i] };
    }
    return null;
  }
  function policyResult(p: Obj) {
    const problems: Obj[] = [];
    const caps: Obj = {};
    for (const lv of LEVELS) {
      caps[lv] = {};
      for (const c of CAPS) {
        const own = p.defaults?.[lv]?.[c] ?? null, b = boundOf(p, lv, c);
        const below = (BELOW[`${lv}|${c}`] ?? []).map((x) => ({ ...x, exceeds: b != null && x.value > b.value })) as Obj[];
        caps[lv][c] = { default: { value: own, source: own != null ? lv : null }, maximum: { value: b?.value ?? null, source: b?.level ?? null }, below };
        if (own != null && b && own > b.value) problems.push({ path: `defaults.${lv}.${c}`, field: `defaults.${lv}.${c}`, message: `defaults.${lv}.${c} ${own} is above maxima.${b.level}.${c} ${b.value}`, file: "policy.yaml", line: 1, col: 1, scope: "limits", level: lv });
        for (const x of below) if (x.exceeds) problems.push({ path: `maxima.${b!.level}.${c}`, field: null, message: `chain ${x.chain} sets ${x.path} ${c} ${x.value}, above maxima.${b!.level}.${c} ${b!.value}`, file: "policy.yaml", line: 1, col: 1, scope: "limits", level: lv, chain: x.chain });
      }
    }
    for (let i = 1; i < LEVELS.length; i++) for (const c of CAPS) for (const k of ["defaults", "maxima"]) {
      const a = p[k]?.[LEVELS[i - 1]]?.[c], b = p[k]?.[LEVELS[i]]?.[c];
      if (a != null && b != null && b > a) problems.push({ path: `${k}.${LEVELS[i]}.${c}`, field: `${k}.${LEVELS[i]}.${c}`, message: `${k}.${LEVELS[i]}.${c} ${b} is above ${LEVELS[i - 1]}'s ${a}`, file: "policy.yaml", line: 1, col: 1, scope: "limits", level: LEVELS[i] });
    }
    const leaf = (v: unknown, path: string) => ({ value: v ?? null, source: get(p, path) != null ? "policy" : "default" });
    const lv = (key: string, node: string, attempts: number, wall: number) => ({ key, chain: "default", node, attempts: { value: attempts, source: p.loops?.[key] ? "loops" : "default" }, wall_clock_s: { value: wall, source: p.loops?.[key] ? "loops" : "default" } });
    const live = [lv("verification.fix_loop", "verification", p.loops?.["verification.fix_loop"]?.attempts ?? p.default.attempts, p.loops?.["verification.fix_loop"]?.wall_clock_s ?? p.default.wall_clock_s), lv("merge_request_feedback.fix_loop", "merge_request_feedback", p.loops?.["merge_request_feedback.fix_loop"]?.attempts ?? p.default.attempts, p.loops?.["merge_request_feedback.fix_loop"]?.wall_clock_s ?? p.default.wall_clock_s)];
    const entries = Object.entries(p.loops ?? {}).map(([key, c]: [string, any]) => ({ key, attempts: c.attempts, wall_clock_s: c.wall_clock_s, live: live.some((x) => x.key === key) }));
    for (const e of entries) if (!e.live) problems.push({ path: `loops.${e.key}`, field: `loops.${e.key}`, message: `loops.${e.key} names no fix loop in the library (a loop is <node id>.fix_loop)`, file: "policy.yaml", line: 1, col: 1, scope: "loops", level: null });
    const items = [p.budget?.work_item_usd, p.maxima?.work_item?.budget_usd, p.defaults?.work_item?.budget_usd].map((v, i) => [["budget.work_item_usd", "maxima.work_item.budget_usd", "defaults.work_item.budget_usd"][i], v] as const).filter(([, v]) => v != null);
    const bind = items.sort((a, b) => (a[1] as number) - (b[1] as number))[0];
    return {
      model: { "policy.yaml": p }, problems, sources: {}, impact: null, warnings: [], policy_values: { auto_escalate_delay_s: 0, auto_review_attempts: 1 },
      resolved: {
        limits: {
          caps, work_item_usd: bind ? { value: bind[1], source: "policy", binding: { key: bind[0], value: bind[1] } } : { value: null, source: "default", binding: null },
          daily_usd: leaf(p.budget?.daily_usd, "budget.daily_usd"),
          max_attempts: { default: leaf(p.defaults?.max_attempts, "defaults.max_attempts"), maximum: leaf(p.maxima?.max_attempts, "maxima.max_attempts") },
          timeout_minutes: { default: leaf(p.defaults?.timeout_minutes, "defaults.timeout_minutes"), maximum: leaf(p.maxima?.timeout_minutes, "maxima.timeout_minutes") },
        },
        loops: { default: { attempts: p.default?.attempts, wall_clock_s: p.default?.wall_clock_s }, entries, live },
        escalation: { auto_escalate_stuck: leaf(p.auto_escalate_stuck ?? true, "auto_escalate_stuck"), auto_escalate_stuck_cap: leaf(p.auto_escalate_stuck_cap ?? 3, "auto_escalate_stuck_cap"), auto_escalate_delay_s: leaf(p.auto_escalate_delay_s ?? 0, "auto_escalate_delay_s"), auto_review_attempts: leaf(1, "auto_review_attempts") },
        retries: { rate_limit_retries: leaf(p.rate_limit_retries ?? 5, "rate_limit_retries"), forge_cli_timeout_s: leaf(p.forge_cli_timeout_s ?? 120, "forge_cli_timeout_s") },
        housekeeping: { max_concurrent: leaf(p.max_concurrent ?? 3, "max_concurrent"), archive_after_days: leaf(p.archive?.after_days, "archive.after_days") },
        findings: { loop_severities: leaf(p.findings?.loop_severities ?? ["critical", "important"], "findings.loop_severities") },
        harnesses: { defaults: {}, maxima: {} }, other: { findings: p.findings ?? {}, archive: p.archive ?? {}, max_concurrent: p.max_concurrent, triggers: p.triggers ?? [] },
      },
      changes: keyChanges("policy.yaml", pub.policy, p),
    };
  }

  function intakeResult(i: Obj, p: Obj) {
    const problems: Obj[] = [];
    return {
      model: { "intake.yaml": i, "policy.yaml": p }, problems, sources: {}, impact: null, warnings: [], policy_values: { auto_escalate_delay_s: 0, auto_review_attempts: 1 },
      resolved: { enabled: i.enabled, interval_s: i.interval_s, max_concurrent: p.max_concurrent ?? 3, priority_ceiling: i.priority_ceiling, repos: i.repos ?? [], schedules: (p.triggers ?? []).map((t: Obj, n: number) => ({ index: n, cron: t.cron, repo: t.repo, chain: t.chain, title: t.title, description: t.description ?? "" })) },
      changes: [...keyChanges("intake.yaml", pub.intake, i), ...keyChanges("policy.yaml", pickIntakePolicy(pub.policy), pickIntakePolicy(p))],
    };
  }
  const pickIntakePolicy = (p: Obj) => ({ max_concurrent: p.max_concurrent, triggers: p.triggers });

  const resultOf = (area: string): Obj => area === "repos" ? repoResult(draft.repos ?? pub.repos) : area === "policy" ? policyResult(draft.policy ?? pub.policy) : intakeResult(draft.intake ?? pub.intake, draftPolicyForIntake ?? pub.policy);
  const files = (area: string) => area === "repos" ? filesOf("repos", draft.repos ?? pub.repos) : area === "policy" ? filesOf("policy", draft.policy ?? pub.policy) : filesOf("intake", draft.intake ?? pub.intake, draftPolicyForIntake ?? pub.policy);
  const publishedFiles = (area: string) => area === "repos" ? filesOf("repos", pub.repos) : area === "policy" ? filesOf("policy", pub.policy) : filesOf("intake", pub.intake, pub.policy);
  const view = (area: string) => ({ area, key: area, draft: draft[area] !== null, files: files(area), base: Object.fromEntries(Object.keys(files(area)).map((f) => [f, "9f2c".padEnd(64, "0")])), published: publishedFiles(area), updated_at: draft[area] ? "2026-10-01T09:12:00Z" : null, result: resultOf(area) });

  const snapshot = (area: string) => JSON.stringify(area === "intake" ? { i: draft.intake, p: draftPolicyForIntake } : draft[area]);
  const restore = (area: string, s: string) => { const v = JSON.parse(s); if (area === "intake") { draft.intake = v.i; draftPolicyForIntake = v.p; } else draft[area] = v; };
  const begin = (area: string) => {
    if (draft[area] === null) {
      draft[area] = clone(area === "repos" ? pub.repos : area === "policy" ? pub.policy : pub.intake);
      if (area === "intake") draftPolicyForIntake = clone(pub.policy);
    }
  };
  const settle = (area: string) => {
    // A draft equal to the published files is no draft.
    const same = JSON.stringify(files(area)) === JSON.stringify(publishedFiles(area));
    if (same) { draft[area] = null; if (area === "intake") draftPolicyForIntake = null; }
  };

  function apply(area: string, ops: Obj[]): string | null {
    for (const o of ops) {
      if (area === "repos") {
        const d = draft.repos as Obj[];
        const at = d.find((r) => r.path === o.path);
        if (o.op === "add_repo") d.push({ path: o.path, enabled: false, managed: true, ...(o.fields ?? {}) });
        else if (o.op === "remove_repo") draft.repos = d.filter((r) => r.path !== o.path);
        else if (o.op === "connect_detected" && at) at.managed = true;
        else if (o.op === "set_repo" && at) for (const [k, v] of Object.entries(o.patch ?? {})) { if (v === null) delete at[k]; else at[k] = v; }
        else return `no op ${o.op}`;
      } else if (area === "policy") {
        const p = draft.policy as Obj;
        if (o.op === "set_value") put(p, o.key, o.value);
        else if (o.op === "set_loop") p.loops = { ...(p.loops ?? {}), [o.key]: { ...(p.loops?.[o.key] ?? p.default), ...(o.max_attempts != null ? { attempts: o.max_attempts } : {}), ...(o.wall_clock_s != null ? { wall_clock_s: o.wall_clock_s } : {}) } };
        else if (o.op === "remove_loop") { if (!p.loops?.[o.key]) return `${o.key} is not in loops`; delete p.loops[o.key]; if (!Object.keys(p.loops).length) delete p.loops; }
        else return `no op ${o.op}`;
      } else {
        const i = draft.intake as Obj, p = draftPolicyForIntake as Obj;
        if (o.op === "set_intake") for (const [k, v] of Object.entries(o.patch ?? {})) { if (k === "max_concurrent") p.max_concurrent = v; else i[k] = v; }
        else if (o.op === "add_schedule") (p.triggers ??= []).push({ cron: o.cron, repo: o.repo, chain: o.chain, title: o.title, description: o.description ?? "" });
        else if (o.op === "set_schedule") { if (!p.triggers?.[o.index]) return `there is no schedule ${o.index}`; Object.assign(p.triggers[o.index], o.patch); }
        else if (o.op === "remove_schedule") { if (!p.triggers?.[o.index]) return `there is no schedule ${o.index}`; p.triggers.splice(o.index, 1); if (!p.triggers.length) delete p.triggers; }
        else return `no op ${o.op}`;
      }
    }
    return null;
  }

  const preview = (chain: string) => {
    const p = draft.policy ?? pub.policy;
    const caps = (level: string, own: Obj = {}) => Object.fromEntries(CAPS.map((c) => {
      const b = boundOf(p, level, c), v = own[c] ?? p.defaults?.[level]?.[c] ?? b?.value ?? null;
      return [c, { value: v, source: own[c] != null ? own.__src : v != null ? "policy" : "default", maximum: b, exceeds: v != null && b != null && v > b.value }];
    }));
    const scope = (path: string, kind: string, level: string, own?: Obj) => ({ path, kind, level, caps: caps(level, own) });
    const nodes: [string, string, Obj?][] = chain === "docs_only"
      ? [["spec", "node"], ["implementation", "node"], ["draft_merge_request", "node"]]
      : [["spec", "node"], ["plan", "node"], ["implementation", "node"], ["verification", "node"], ["draft_merge_request", "node"], ["merge_request_feedback", "node"]];
    const out = [scope(chain, "chain", "work_item")];
    for (const [n, kind] of nodes) {
      out.push(scope(n, kind, "nodes"));
      if (n === "implementation") out.push(scope("implementation.main.implement", "task", "tasks", { time_cap_minutes: 120, __src: "library:tasks.implementer" }));
      if (n === "verification" && chain !== "docs_only") out.push(scope("verification.main", "step", "steps", { time_cap_minutes: 100, __src: "chain" }));
    }
    return { chain, scopes: out };
  };

  return {
    list: () => (["repos", "policy", "intake"] as const).filter((a) => draft[a] !== null).map((a) => ({ area: a, key: a, files: Object.keys(files(a)).sort(), changes: resultOf(a).changes.length, problems: resultOf(a).problems.length, updated_at: "2026-10-01T09:12:00Z" })),
    /** `[status, body]`, or null for a request that is not this mock's. */
    handle(area: string, action: string | undefined, file: string | undefined, method: string, body: Obj, q: URLSearchParams): [number, unknown] | null {
      if (method === "DELETE") { draft[area] = null; if (area === "intake") draftPolicyForIntake = null; undo[area] = []; return [204, undefined]; }
      if (action === "preview") return [200, preview(q.get("chain") ?? "default")];
      if (action === "fragment") {
        const e = (draft.repos ?? pub.repos).find((r: Obj) => r.path === q.get("path"));
        return e ? [200, { path: q.get("path"), text: yamlOf(e) }] : [404, { detail: `nothing at '${q.get("path")}' in repos 'repos'` }];
      }
      if (action === "publish") {
        const r = resultOf(area);
        if (r.problems.length) return [422, { detail: `${r.problems.length} problem(s) to fix before publishing`, problems: r.problems }];
        const written = Object.keys(files(area));
        if (area === "repos") pub.repos = clone(draft.repos); else if (area === "policy") pub.policy = clone(draft.policy); else { pub.intake = clone(draft.intake); pub.policy = clone(draftPolicyForIntake); }
        draft[area] = null; draftPolicyForIntake = null; undo[area] = [];
        return [200, { published: written, result: resultOf(area) }];
      }
      if (action === "rebase") return [200, view(area)];
      if (action === "undo") {
        const s = undo[area].pop();
        if (s === undefined) return [409, { detail: "nothing to undo" }];
        restore(area, s); settle(area); return [200, view(area)];
      }
      if (file && method === "PUT") return [200, view(area)]; // the YAML view's typing: the mock shows the text, not a parse
      if (action === "ops") {
        const ops: Obj[] = body.ops ?? [];
        const saved = snapshot(area);
        begin(area);
        const before = snapshot(area);
        const bad = apply(area, ops);
        if (bad) { restore(area, saved); return [422, { detail: bad, op: 0 }]; }
        if (q.get("preview")) { const v = { ...view(area), ops: ops.map((o) => ({ op: o.op })) }; restore(area, saved); return [200, v]; }
        undo[area].push(before);
        settle(area);
        return [200, { ...view(area), ops: ops.map((o) => ({ op: o.op })) }];
      }
      return [200, view(area)];
    },
    checks: () => [
      { id: "c3", at: "2026-10-01T10:05:00Z", ready: 4, started: ["kraft-d71a"], skipped: [{ bead_id: "kraft-e1", reason: "above_priority_ceiling" }, { bead_id: "kraft-e2", reason: "above_priority_ceiling" }, { bead_id: "kraft-e3", reason: "already_item" }] },
      { id: "c2", at: "2026-10-01T10:00:00Z", ready: 3, started: [], skipped: [{ bead_id: "kraft-f1", reason: "max_concurrent" }, { bead_id: "kraft-f2", reason: "max_concurrent" }, { bead_id: "kraft-f3", reason: "max_concurrent" }] },
    ],
    /** GET /policy: the shipped route, for the slot count. */
    activeCount: () => 3,
  };
}
