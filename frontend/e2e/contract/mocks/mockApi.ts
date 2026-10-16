import { NG_CHAINS, NG_REPOS, NG_WORKSPACES, ngDryRun } from "./ngBoard";
import { artifactFor, compareFor, diffFor, fixTargetFor, documentDetail, searchFor, type Scenario } from "./fixtures";
import { makeAreas } from "./areasMock";
import { ngThreads } from "./ngItems";
import { harnessesServer, PROVIDER_STATUS } from "./ngHarnesses";
import { motion, type Motion, type MotionOptions } from "./motion";
import { listRow, transitions, type Ev } from "./transitions";
import draftViews from "./draftViews.json" with { type: "json" };

/**
 * The mocked Kraft API with no transport of its own: a request in, an answer out. Two callers put it on the
 * wire: the UI contract suite through Playwright's routes (playwright.ts), and the dashboard demo through a
 * fetch and WebSocket shim in the page itself (frontend/demo/), so the demo shows what CI checks.
 */

/** One request to `/api/**`. `json()` is the body parsed, or null when there is none. */
export interface MockRequest { method: string; url: string; json: () => any }
/** An answer: an HTTP response, a connection refused (`ABORT`), or no answer ever (`HANG`). */
export type Answer = { status: number; contentType?: string; headers?: Record<string, string>; body?: string };
export const ABORT = Symbol("connection refused");
export const HANG = Symbol("never answers");
export type Reply = Answer | typeof ABORT | typeof HANG;
export type MockServer = (req: MockRequest) => Promise<Reply>;

export interface MockOptions {
  /** Every call except /health answers 401 → the Login screen. */
  locked?: boolean;
  /** What POST /login answers while locked: 200, a 401, or a 429 with Retry-After (default 200). */
  login?: "ok" | "wrong" | "locked";
  /** ux2-W6: GET /work-items answers the board's fixtures (`S.ngBoard`, `S.ngArchived`, each row read from its bundle); "empty" answers none. */
  ngBoard?: boolean | "empty";
  /** ux2-W6 board states. `loading`: boot's list read fails, every later one never answers.
   *  `offline`: boot's read answers, every later one, and every draft read, fails to connect, and the event socket closes. */
  boardState?: "loading" | "offline";
  /** ux2-W15: the repos, policy and intake drafts, answered as W13's server does (areasMock.ts); the value seeds the cell's state. */
  areas?: "default";
  /** ux2-W14: the Harnesses page's draft (ngHarnesses.ts): `floor`, a clean install. Unset: the routes answer as before. */
  harnesses?: "floor";
  /** ux2-W11: the running item's chain draft. Unset or `none`: no draft (the + seam's menu reads the real library). */
  itemDraft?: "none" | "changes" | "problems";
  /** ux2-W12: the Library's draft. `draft`: three changes, one a new component. */
  ngLibrary?: "draft";
  /** The release the server says it runs, in /health and /update, up to date. Unset: the scenario's, an update behind. */
  version?: string;
  /** The instant an action's events and `updated_at` record (transitions.ts). Unset: the page's clock. */
  now?: () => string;
  /** Told of every event an action appends (transitions.ts), as the server's event socket would send it. The demo
   *  forwards each to its open sockets; the contract suite's socket stays silent. */
  onEvent?: (e: Ev) => void;
  /** Live motion (motion.ts): the board's active items walk their chains on a timer and stop at gates, a waiting one
   *  goes on once its retry time passes, and a fresh item is filed when the board goes quiet. Off unless set; only the demo sets it. An object paces it, for tests. */
  motion?: boolean | MotionOptions;
}

/** The ops each `itemDraft` state starts from (the running item stands on `verification`). */
const ITEM_DRAFT_SEEDS: Record<string, any[]> = {
  changes: [
    { op: "override", path: "merge_request.open.open_draft", task_config: { model: "opus" } },
    { op: "override", path: "mr_checks", policy: { time_cap_minutes: 45 } },
    { op: "add_node", after: "work_brief", node: { id: "security_scan", extends: "security_scan" } },
  ],
  problems: [{ op: "override", path: "merge_request.open.open_draft", task_config: { command: "make" } }],
};

/** The server's `passed` rule (src/kraft/drafts/item.py): an op is passed once the run stands at or after where it acts. */
const draftPassed = (op: any, nodes: any[], cur: string | null) => {
  if (!cur) return false;
  const ids = nodes.map((n) => n.id);
  if (!ids.includes(cur)) return true;
  const node = op.op === "add_node" ? op.after : op.op === "remove_node" ? op.node : op.path.split(".")[0];
  if (!ids.includes(node)) return false;
  return op.op === "add_node" ? ids.indexOf(node) < ids.indexOf(cur) : ids.indexOf(node) <= ids.indexOf(cur);
};
/** The one refusal the mock knows: a `command` override on a builtin task. */
const draftProblems = (ops: any[]) => ops.flatMap((op, i) => (!op.passed && op.op === "override" && op.task_config?.command && /open_draft/.test(op.path) ? [{ op: i, message: "task_config.command: not a field of a builtin task" }] : []));
/** The chain with each added node placed after its `after`. */
const draftChain = (nodes: any[], ops: any[]) => {
  const out = [...nodes];
  for (const op of ops) if (op.op === "add_node") out.splice(out.findIndex((n) => n.id === op.after) + 1, 0, { id: op.node.id, kind: "exec", tasks: [`${op.node.id}.scan.run`], steps: [[`${op.node.id}.scan.run`]], gate_after: null });
  return out;
};

/** A chain file as its author would write it: one mapping per node, nulls left out. */
const chainYaml = (nodes: Record<string, unknown>[]) =>
  "nodes:\n" + nodes.map((n) => Object.entries(n).filter(([, v]) => v != null)
    .map(([k, v], i) => `${i ? "    " : "  - "}${k}: ${Array.isArray(v) ? `[${v.join(", ")}]` : v}`).join("\n")).join("\n") + "\n";

/** Real W9 answers for the Chains editor (the shipped default chain on a test
 *  server): `default` with a change, an added gate and a removed one; `broken`
 *  with an empty exec node; `yaml-error`; and the 409 a stale publish answers. */
const DRAFTS: any = draftViews;
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
/** The Library's draft for ux2-W12, built from the real published library (draftViews.json): the model is each
 *  component's definition as written. `add_component` and `set_field` apply; everything else is a no-op the flows don't send. */
function libraryState() {
  const model: Record<string, Record<string, any>> = {};
  for (const c of DRAFTS.library.components) (model[c.kind] ??= {})[c.name] = clone(c.definition);
  const changes: any[] = [];
  const problems: any[] = [];
  let drafted = true;
  (model.tasks ??= {}).fixer = { kind: "agent", prompt: "Fix what the review found." };
  (model.nodes ??= {}).approval = { kind: "gate", message: "Review and approve." };
  (model.steps ??= {}).checks = { tasks: [{ id: "lint", extends: "verify_changed_scopes" }] };
  changes.push(
    { path: "tasks.implementer.prompt", kind: "change", summary: "prompt", fields: ["prompt"], reaches: ["default", "quick-task"] },
    { path: "steering.project-standards", kind: "change", summary: "instructions", fields: ["instructions"], reaches: ["default"] },
    { path: "tasks.fixer", kind: "add", summary: "added", reaches: [] },
    { path: "nodes.approval", kind: "add", summary: "added", reaches: [] },
    { path: "steps.checks", kind: "add", summary: "added", reaches: [] },
  );
  const state = {
    text: "# library.yaml\n",
    view() {
      return {
        area: "library", key: "library", draft: drafted, files: { "library.yaml": state.text }, base: { "library.yaml": "9f2c".padEnd(64, "0") },
        updated_at: drafted ? "2026-10-01T09:12:00Z" : null,
        result: {
          model: { "library.yaml": clone(model) }, resolved: null, sources: {}, warnings: [], yaml_error: undefined,
          policy_values: { auto_escalate_delay_s: 0, auto_review_attempts: 1 },
          impact: { chains: drafted ? ["default", "quick-task"] : [], repos: drafted ? ["/Users/me/code/kraft"] : [] },
          problems: clone(problems), changes: clone(changes),
        },
      };
    },
    fragment: (path: string) => {
      const [section, name] = path.split(".");
      return Object.entries(model[section]?.[name] ?? {}).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("\n") + "\n";
    },
    /** A refusal's detail, the op's own answer, or nothing. */
    apply(o: Record<string, unknown>): string | Record<string, unknown> | undefined {
      if (o.op === "add_component") {
        const { section, name, kind } = o as { section: string; name: string; kind?: string };
        if (model[section]?.[name]) return `${name} is taken`;
        (model[section] ??= {})[name] = section === "steering" ? { instructions: "" } : section === "steps" ? { tasks: [] } : section === "nodes" ? (kind === "gate" ? { kind: "gate" } : { kind: "exec", steps: [] }) : kind ? { kind } : {};
        changes.push({ path: `${section}.${name}`, kind: "add", summary: "added", reaches: [] });
        drafted = true;
        return { path: `${section}.${name}` };
      }
      if (o.op === "rename") {
        const [section, name] = String(o.path).split(".");
        const to = String(o.id);
        if (model[section]?.[to]) return `${to} is taken`;
        const own = model[section]?.[name];
        if (!own) return `nothing at ${o.path}`;
        model[section][to] = own;
        delete model[section][name];
        // Library-internal references follow; the chains that use it are rewritten in the same draft.
        const refs: { file: string; path: string; field: string }[] = [];
        for (const sec of Object.values(model)) for (const [k, c] of Object.entries(sec)) if (c?.extends === name) { c.extends = to; refs.push({ file: "library.yaml", path: k, field: "extends" }); }
        const published = DRAFTS.library.components.find((c: any) => c.id === o.path);
        for (const u of published?.used_by_paths ?? []) refs.push({ file: `chains/${u.chain}.yaml`, path: u.path, field: "extends" });
        changes.push({ path: `${section}.${name}`, kind: "remove", summary: "renamed", reaches: published?.used_by ?? [] }, { path: `${section}.${to}`, kind: "add", summary: `renamed from ${name}`, reaches: published?.used_by ?? [] });
        drafted = true;
        return { updated: refs };
      }
      if (o.op === "remove") {
        const [section, name] = String(o.path).split(".");
        if (!model[section]?.[name]) return `nothing at ${o.path}`;
        delete model[section][name];
        const published = DRAFTS.library.components.find((c: any) => c.id === o.path);
        changes.push({ path: String(o.path), kind: "remove", summary: "removed", reaches: published?.used_by ?? [] });
        drafted = true;
        return { broken: (published?.used_by_paths ?? []).map((u: any) => ({ file: `chains/${u.chain}.yaml`, path: u.path, field: "extends" })) };
      }
      if (o.op === "set_field") {
        const [section, name] = String(o.path).split(".");
        const at = model[section]?.[name];
        if (!at) return `nothing at ${o.path}`;
        if (o.value === null || o.value === "") delete at[String(o.field)];
        else at[String(o.field)] = o.value;
        if (!changes.some((c) => c.path === `${section}.${name}`)) changes.push({ path: `${section}.${name}`, kind: "change", summary: String(o.field), fields: [o.field], reaches: [] });
        drafted = true;
      }
      return undefined;
    },
  };
  return state;
}

/** The `default` view under another key: `stale` is it with a publish that 409s. */
const chainView = (key: string) => {
  // A new chain before its first node: what `new_chain` leaves (no node, nothing resolves yet).
  if (key === "empty") return { area: "chains", key, draft: true, files: { "chains/empty.yaml": "id: empty\ndescription: ''\nnodes: []\n" }, base: { "chains/empty.yaml": null }, updated_at: null, result: { ...clone(DRAFTS.views.broken.result), model: { "chains/empty.yaml": { id: "empty", description: "", nodes: [] } }, resolved: null, sources: {}, problems: [], changes: [], warnings: [] } };
  const v = DRAFTS.views[key] ?? DRAFTS.views.default;
  if (DRAFTS.views[key]) return clone(v);
  const s = JSON.stringify(v).replaceAll("chains/default.yaml", `chains/${key}.yaml`);
  const out = JSON.parse(s);
  out.key = key;
  out.result.model[`chains/${key}.yaml`].id = key;
  return out;
};
/** Block YAML of a mapping, for the fragment route's mock answer only (the server writes the real one). */
const yamlOf = (v: unknown, ind = ""): string => {
  if (Array.isArray(v)) return v.map((x) => (x && typeof x === "object" ? `${ind}- ${yamlOf(x, `${ind}  `).trimStart()}` : `${ind}- ${x}\n`)).join("");
  if (v && typeof v === "object") return Object.entries(v).map(([k, x]) => (x && typeof x === "object" ? `${ind}${k}:\n${yamlOf(x, `${ind}  `)}` : `${ind}${k}: ${x}\n`)).join("");
  return `${ind}${v}\n`;
};
/** The authored component at a canonical path in a view's model, as the fragment route answers it. */
function fragmentOf(view: ReturnType<typeof chainView>, path: string) {
  const [id, ...rest] = path.split(".");
  let at = (view.result.model[`chains/${view.key}.yaml`].nodes as Record<string, any>[]).find((n) => n.id === id);
  for (const seg of rest) at = at?.[seg] ?? at?.steps?.find((s: { id: string }) => s.id === seg) ?? at?.tasks?.find((x: { id: string }) => x.id === seg);
  return at ? yamlOf(at) : `id: ${rest.pop() ?? id}\n`;
}
/** An op answered as the server would, for the ops the contract's rows send. */
function applyOps(view: ReturnType<typeof chainView>, ops: Record<string, unknown>[]) {
  const file = `chains/${view.key}.yaml`;
  const nodes = view.result.model[file].nodes as Record<string, unknown>[];
  for (const o of ops) {
    if (o.op === "add_node") {
      nodes.splice(Number(o.at), 0, o.kind === "gate" ? { id: o.id, kind: "gate" } : { id: o.id, kind: "exec", steps: [] });
      view.result.changes.push({ path: o.id, kind: "add", summary: "added" });
      if (o.kind !== "gate") {
        view.result.problems.push({ path: o.id, field: null, message: "Value error, 'steps' must not be empty", file, line: 3, col: 5 });
        view.result.resolved = null;
        view.result.sources = {};
      }
    }
  }
  for (const o of ops) {
    if (o.op !== "move_to_library") continue;
    // R47: the node or task becomes a component of library.yaml and the chain keeps `{ id, extends }`; the library joins the draft.
    const [id, step, task] = String(o.path).split(".");
    const node = nodes.find((n) => n.id === id) as Record<string, any>;
    const container = task ? node.steps.find((x: any) => x.id === step).tasks : nodes;
    const at = container.findIndex((x: any) => x.id === (task ?? id));
    const { id: _id, ...body } = container[at];
    container[at] = { id: task ?? id, extends: o.name };
    const section = task ? "tasks" : "nodes";
    view.files["library.yaml"] = `${section}:\n  ${o.name}: ${JSON.stringify(body)}\n`;
    view.base["library.yaml"] = "9f2c".padEnd(64, "0");
    view.result.model["library.yaml"] = { [section]: { [String(o.name)]: body } };
    view.result.changes.push({ path: String(o.path), kind: "change", summary: `extends ${o.name}`, fields: ["extends"] });
  }
  view.draft = true;
  return view;
}

const json = (body: unknown, status = 200): Answer => ({ status, contentType: "application/json", body: JSON.stringify(body) });

/**
 * Serve the whole /api surface from the scenario. A work item's actions change it as the server would
 * (transitions.ts), for the page's life: the list, the detail and the events all read the one bundle.
 * Most other mutating verbs return a plausible 200 so a Submit never crashes, and change nothing.
 */
export function mockServer(S: Scenario, opts: MockOptions = {}): MockServer & { motion: Motion | null } {
  let restarted = 0;
  const hs = opts.harnesses ? harnessesServer() : null;
  let listReads = 0;
  const itemDrafts = new Map<string, any[]>();
  // Always answering: a shell cell may open Policy (a built page), and the shipped UI never asks for /drafts/(repos|policy|intake).
  const areas = makeAreas();

  const viewedMarks = new Set<string>();
  const lib = opts.ngLibrary ? libraryState() : null;
  // ux2-W8: review threads per item, seeded for a needs-gate item on first read.
  const threads: Record<string, any[]> = {};
  // W5b's gate-pane thread (the bundle's) comes first, in the full thread shape, then W8's review set.
  const threadsOf = (wid: string) =>
    (threads[wid] ??= [
      ...(S.bundles[wid]?.threads ?? []).map((t: any) => ({ anchor_sha: "9c8d7e6", resolved_at: null, draft: false, ...t, comments: t.comments.map((c: any) => ({ thread_id: t.id, review_id: "r1", attempt: null, suggestion: null, claim: null, draft: false, ...c })) })),
      ...(S.bundles[wid]?.item.pending_gate === "final_review" && Object.values(S.ng).includes(wid)
        ? ngThreads(wid, compareFor(wid, S.variant).files.map((f: { path: string }) => f.path).sort(treeOrder))
        : []),
    ]);
  // The review tree's order: by folder, the top level last, so the seeded threads land on the file shown first.
  const dirOf = (p: string) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/") + 1) : "\uffff");
  const treeOrder = (a: string, b: string) => dirOf(a).localeCompare(dirOf(b)) || a.localeCompare(b);
  const findThread = (tid: string) => Object.values(threads).flat().find((t) => t.id === tid);
  const findComment = (cid: string) => Object.values(threads).flat().flatMap((t) => t.comments).find((c) => c.id === cid);
  const mustFix = (wid: string) => threadsOf(wid).filter((t) => t.label === "must_fix" && t.state !== "resolved").map((t) => t.id);
  // Every item action, the bulk door's included, goes through these: one item, read by the list and the detail alike.
  const T = transitions(S, { now: opts.now, mustFix, onEvent: opts.onEvent });
  const send = (r: { status: number; body: unknown }) => json(r.body, r.status);
  let seq = 0;
  const serve: MockServer = async (req) => {
    const url = new URL(req.url);
    const p = url.pathname.replace(/^.*?\/api/, "");
    const method = req.method;
    const q = url.searchParams;

    if (p === "/health") {
      if (restarted && restarted++ <= 3) return ABORT;
      return json(opts.version ? { ...S.settings.health, version: opts.version, installed: opts.version } : S.settings.health);
    }
    if (opts.locked) {
      if (p === "/login" && method === "POST") {
        if (opts.login === "wrong") return json({ detail: "Wrong password." }, 401);
        if (opts.login === "locked") return { status: 429, contentType: "application/json", headers: { "Retry-After": "125" }, body: JSON.stringify({ detail: "Too many failed logins. Try again later." }) };
        return json({ ok: true });
      }
      return json({ detail: "unauthenticated" }, 401);
    }

    let m: RegExpMatchArray | null;

    /* budget */
    if (p === "/budget/today") return json({ spent_usd: 8.3, cap_usd: 50 });

    /* work items */
    if (opts.boardState === "offline" && p.startsWith("/drafts/")) return ABORT;
    if (p === "/work-items" && method === "GET" && opts.boardState && q.get("archived") !== "true") {
      const n = ++listReads;
      if (opts.boardState === "offline" && n > 1) return ABORT;
      if (opts.boardState === "loading") return n === 1 ? ABORT : HANG; // later reads never answer
    }
    if (p === "/work-items" && method === "GET") {
      const arch = q.get("archived") === "true";
      const ids = opts.ngBoard === "empty" ? [] : arch ? S.ngArchived : S.ngBoard;
      const list = opts.ngBoard ? ids.map((id) => listRow(S.bundles[id].item)) : arch ? S.archived : S.items;
      return json({ items: list, cursor: T.cursor() });
    }
    if (p === "/work-items" && method === "POST" && q.get("dry_run") && opts.ngBoard) {
      const r = ngDryRun(req.json() ?? {});
      return json(r.body, r.status);
    }
    // ux2-W6: a create from the composer files a new item, on the chain its dry run showed.
    if (p === "/work-items" && method === "POST" && !q.get("dry_run") && opts.ngBoard) {
      return send(T.create(req.json() ?? {}));
    }
    if (p === "/work-items" && method === "POST" && q.get("dry_run")) {
      const nodes = S.items[0]?.chain_definition?.nodes ?? [];
      return json({
        dry_run: true,
        nodes,
        skipped: [],
        gates: nodes.filter((n: any) => n.gate_after === n.id).map((n: any) => n.id),
        caps: { budget_usd: 5, budget_source: "policy", daily_usd: 50, nodes: {} },
      });
    }
    if (p === "/work-items" && method === "POST") return json({ id: S.items[0]?.id ?? "00000000000000000000000000000000" });
    if (method === "POST" && p === "/work-items/bulk") {
      // B9: each id through its own route's door (`lifecycle._bulk_one`), its refusal a result rather than the end.
      const { action, ids = [], reason } = req.json() ?? {};
      if (action === "cancel" && !String(reason ?? "").trim()) return json({ detail: "reason: cancel needs a non-blank reason" }, 422);
      const door = (id: string) => action === "pause" ? T.pause(id) : action === "cancel" ? T.end(id, "cancel", reason) : action === "archive" ? T.archive(id) : T.restore(id);
      return json({ results: (ids as string[]).map((id) => {
        const r = door(id);
        const status = S.bundles[id]?.item.status;
        if (r.status === 404) return { id, ok: false, error: "not found" };
        return r.status < 300 ? { id, ok: true, status } : { id, ok: false, status, error: r.body.detail };
      }) });
    }
    if ((m = p.match(/^\/work-items\/([^/]+)$/))) {
      const b = S.bundles[m[1]];
      if (!b) return json({ detail: "unknown work item" }, 404);
      if (method === "PATCH") return send(T.patch(m[1], req.json() ?? {}));
      return json({ ...b.item, worker_sessions: b.sessions });
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/events$/))) {
      const b = S.bundles[m[1]];
      const all = b?.events ?? [];
      const limit = q.get("limit") ? Number(q.get("limit")) : undefined;
      const before = q.get("before_seq") ? Number(q.get("before_seq")) : undefined;
      if (before !== undefined) {
        const page = all.filter((e) => e.seq < before);
        return json(page.slice(-(limit ?? 100)));
      }
      const after = Number(q.get("after_seq") ?? 0);
      const page = all.filter((e) => e.seq > after);
      return json(limit !== undefined ? page.slice(0, limit) : page);
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/documents$/))) {
      return json({ work_item_id: m[1], documents: S.docs[m[1]] ?? [] });
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/diff$/))) return json(diffFor(m[1], S.variant));
    if ((m = p.match(/^\/work-items\/([^/]+)\/compare$/))) {
      return json(compareFor(m[1], S.variant, ["1", "true"].includes(q.get("ignore_whitespace") ?? ""), viewedMarks));
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/viewed$/)) && (method === "PUT" || method === "DELETE")) {
      const file = q.get("file") ?? "";
      viewedMarks[method === "PUT" ? "add" : "delete"](`${m[1]}|${file}`);
      return json({ file, to: q.get("to") ?? "latest", viewed: method === "PUT" });
    }
    /* ux2-W8: review threads and comments, kept for the test's life */
    if ((m = p.match(/^\/work-items\/([^/]+)\/threads$/))) {
      const list = threadsOf(m[1]);
      if (method === "GET") return json(list);
      const b = req.json() ?? {};
      const id = `th-new${seq++}`;
      const t = { id, work_item_id: m[1], gate: S.bundles[m[1]]?.item.pending_gate ?? null, node_id: b.node_id ?? null, file_path: b.file_path ?? null, side: b.side ?? null, start_line: b.start_line ?? null, end_line: b.end_line ?? null, anchor_sha: b.anchor_sha ?? "9c8d7e6", label: b.label ?? null, state: "open", resolved_at: null, created_at: "2026-09-13T10:09:00.000Z", draft: true,
        comments: [{ id: `c-new${seq++}`, thread_id: id, review_id: null, author: "you", attempt: null, body: b.body, suggestion: b.suggestion ?? null, claim: null, created_at: "2026-09-13T10:09:00.000Z", draft: true }] };
      list.push(t);
      return json(t, 201);
    }
    if ((m = p.match(/^\/threads\/([^/]+)(?:\/(comments|resolve|reopen))?$/))) {
      const t = findThread(m[1]);
      if (!t) return json({ detail: `unknown thread ${m[1]}` }, 404);
      const b = method === "GET" ? {} : req.json() ?? {};
      if (m[2] === "comments") {
        const c = { id: `c-new${seq++}`, thread_id: t.id, review_id: null, author: "you", attempt: null, body: b.body, suggestion: b.suggestion ?? null, claim: null, created_at: "2026-09-13T10:09:00.000Z", draft: true };
        t.comments.push(c);
        return json(c, 201);
      }
      if (m[2]) return json(Object.assign(t, { state: m[2] === "resolve" ? "resolved" : "open" }));
      if (method === "DELETE") { threads[t.work_item_id] = threads[t.work_item_id].filter((x) => x !== t); return { status: 204 }; }
      Object.assign(t.comments[0], { body: b.body ?? t.comments[0].body, suggestion: "suggestion" in b ? b.suggestion : t.comments[0].suggestion });
      if ("label" in b) t.label = b.label;
      return json(t);
    }
    // A submitted review publishes the drafts (`review._submit`); at a gate, approve and request_changes are that
    // gate's approve and reject, and with none pending request_changes reruns the node it names.
    if (method === "POST" && (m = p.match(/^\/work-items\/([^/]+)(?:\/gates\/([^/]+))?\/review$/))) {
      const wid = m[1];
      const stop = T.reviewRefusal(wid, m[2] && decodeURIComponent(m[2]));
      if (stop) return send(stop);
      const it = S.bundles[wid].item;
      const { outcome, summary, node } = req.json() ?? {};
      const gate: string | null = m[2] ? decodeURIComponent(m[2]) : it.pending_gate ?? null;
      if (!gate && outcome === "approve") return json({ detail: "nothing to approve: no gate is pending" }, 409);
      const publish = () => {
        for (const t of threadsOf(wid)) { t.draft = false; for (const c of t.comments) if (c.draft) Object.assign(c, { draft: false, review_id: "r2" }); }
        T.append(wid, "review_submitted", { review_id: "r2", gate, outcome }, gate);
      };
      if (outcome === "comment") { publish(); return json({ review_id: "r2", outcome, gate, reply_agent: !!gate }); }
      if (gate) {
        const r = outcome === "approve" ? T.approve(wid, gate) : T.reject(wid, gate, summary || "Changes requested.", node);
        if (r.status === 200) publish();
        return send(r);
      }
      const target = node ?? it.fix_target?.node ?? fixTargetFor(null).node;
      const r = T.requestChanges(wid, target, summary || "Changes requested.");
      if (r.status !== 200) return send(r);
      publish();
      return json({ review_id: "r2", outcome, gate: null, target, target_reason: "current node", action: r.body.action });
    }
    if ((m = p.match(/^\/comments\/([^/]+)$/))) {
      const c = findComment(m[1]);
      if (!c) return json({ detail: `unknown comment ${m[1]}` }, 404);
      const t = findThread(c.thread_id);
      if (method === "DELETE") { t.comments = t.comments.filter((x: any) => x !== c); return { status: 204 }; }
      return json(Object.assign(c, { body: req.json()?.body ?? c.body }));
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/fix-target$/))) {
      return json(fixTargetFor(S.bundles[m[1]]?.item.pending_gate ?? null, q.get("node") ?? undefined));
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/artifact$/))) {
      const b = S.bundles[m[1]];
      return b ? json(artifactFor(b.item, S.variant)) : json({ detail: "no artifact" }, 404);
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/cancel-preview$/))) {
      const b = S.bundles[m[1]];
      if (!b) return json({ detail: "unknown work item" }, 404);
      const it = b.item;
      return json({
        running: it.current_node_id ? { node: it.current_node_id, task: it.current_node_id, attempt: 1 } : null,
        kept: { branch: `kraft/${it.id}`, worktree: `/tmp/kraft/worktrees/${it.id}`, findings: (it.deferred_findings ?? []).length, threads: 0 },
        mr: it.mr_ref ? { ref: it.mr_ref.number, url: it.mr_ref.url, state: "open" } : null,
        spend: { spent_usd: it.budget_cap?.spent_usd ?? 0, cap_usd: it.budget_cap?.cap_usd ?? null },
        beads: b.beads ?? [],
      });
    }
    if (method === "POST" && (m = p.match(/^\/work-items\/([^/]+)\/duplicate$/))) {
      return json({ id: `${m[1]}-dup`, status: "paused" }, 201);
    }
    /* the item chain draft (B15, ux2-W11): none unless `opts.itemDraft` seeds the running item's; then a small stateful server */
    if ((m = p.match(/^\/work-items\/([^/]+)\/draft(\/apply)?$/))) {
      const b = S.bundles[m[1]];
      if (!b) return json({ detail: "unknown work item" }, 404);
      const cur = b.item.current_node_id ?? null;
      const stored = (): any[] => {
        if (!itemDrafts.has(m![1])) itemDrafts.set(m![1], opts.itemDraft && opts.itemDraft !== "none" && m![1] === S.ng.running ? clone(ITEM_DRAFT_SEEDS[opts.itemDraft]) : []);
        return itemDrafts.get(m![1])!;
      };
      const answer = (ops: any[]) => {
        const nodes = b.item.chain_definition?.nodes ?? [];
        const marked = ops.map((op) => ({ ...op, passed: draftPassed(op, nodes, cur) }));
        const problems = draftProblems(marked);
        const live = marked.filter((op, i) => !op.passed && !problems.some((x) => x.op === i));
        const cap = b.item.budget_cap ?? { spent_usd: 1.2, cap_usd: 10 };
        return { ops: marked, problems, checks: { budget: { spent_usd: cap.spent_usd, cap_usd: cap.cap_usd } }, nodes: draftChain(nodes, live), base_seq: ops.length ? 42 : null, updated_at: ops.length ? "2026-10-01T09:12:00Z" : null };
      };
      if (method === "DELETE") { const had = stored().length > 0; itemDrafts.set(m[1], []); return had ? { status: 204 } : json({ detail: "this work item has no draft" }, 404); }
      if (method === "PUT") { itemDrafts.set(m[1], req.json()?.ops ?? []); return json(answer(stored())); }
      if (m[2]) {
        const ops = stored();
        if (!ops.length) return json({ detail: "this work item has no draft" }, 404);
        const v = answer(ops);
        const passed = v.ops.flatMap((op: any, i: number) => (op.passed ? [i] : []));
        if (passed.length) return json({ detail: "the item has moved past some of this draft's ops; remove them or move them after the current node", passed }, 409);
        if (v.problems.length) return json({ detail: `${v.problems.length} problem(s) to fix before applying`, problems: v.problems }, 422);
        b.item.chain_definition.nodes = v.nodes;
        T.append(m[1], "chain_revised", { gate: null, changes: ops, diff: [], source: "draft" });
        itemDrafts.set(m[1], []);
        return json(answer([]));
      }
      return json(answer(stored()));
    }
    // The item's doors, each a transition (transitions.ts): what it changes, its event, its refusal.
    if (method === "POST" && (m = p.match(/^\/work-items\/([^/]+)\/(pause|resume|retry|skip|cancel|complete|abandon|archive|restore|escalate|budget\/raise|gates\/([^/]+)\/(approve|reject))$/))) {
      const [, wid, door, gate, verdict] = m;
      const b = req.json() ?? {};
      if (verdict) return send(verdict === "approve" ? T.approve(wid, decodeURIComponent(gate)) : T.reject(wid, decodeURIComponent(gate), b.note ?? "", b.node));
      switch (door) {
        case "pause": return send(T.pause(wid));
        case "resume": return send(T.resume(wid, b.steer || null));
        case "retry": return send(T.retry(wid, b.steer || null));
        case "skip": return send(T.skip(wid, b.path, b.note ?? null));
        case "cancel": case "complete": return send(T.end(wid, door, b.reason));
        case "abandon": return send(T.abandon(wid));
        case "archive": return send(T.archive(wid));
        case "restore": return send(T.restore(wid));
        case "escalate": return send(T.escalate(wid, b.message, !!b.new_thread));
        default: return send(T.raiseBudget(wid, b.budget_usd ?? null));
      }
    }
    // Reopen MR: its event, and the answer it always had (the item stays where its stop left it).
    if (method === "POST" && (m = p.match(/^\/work-items\/([^/]+)\/reopen-mr$/)) && S.bundles[m[1]]) {
      const mr = S.bundles[m[1]].item.mr_ref;
      T.append(m[1], "mr_reopened", { ref: mr?.number ?? null, url: mr?.url ?? null });
    }
    if (method === "POST" && (m = p.match(/^\/work-items\/([^/]+)\/escalate\/stop$/))) return send(T.stopEscalation(m[1]));
    if ((m = p.match(/^\/work-items\/([^/]+)\/(reopen-mr|open-worktree)$/))) {
      return json({ id: m[1], status: "active", node_id: "implement", loop: "verify_fix_loop", steer: null, path: "/tmp", editor: "code" });
    }

    /* logs: jsonl, plain text, SSE tail */
    if ((m = p.match(/^\/work-items\/([^/]+)\/sessions\/([^/]+)\/log$/))) {
      const sid = m[2];
      const bundle = S.bundles[m[1]];
      const lines = bundle?.logs[sid] ?? [];
      const status = bundle?.sessions.find((s) => s.id === sid)?.status ?? "done";
      if (q.get("follow") === "1") {
        const body = lines.slice(-5).map((l) => `data: ${JSON.stringify(l)}\n\n`).join("") + "event: end\ndata: {}\n\n";
        return { status: 200, contentType: "text/event-stream", body };
      }
      if (q.get("format") === "jsonl") return json({ session_id: sid, status, lines });
      return { status: 200, contentType: "text/plain", body: lines.map((l) => l.text).join("\n") };
    }

    /* documents & search */
    if (p === "/search") {
      const all = Object.values(S.docs).flat();
      return json(searchFor(q.get("q") ?? "", all, S.variant));
    }
    if ((m = p.match(/^\/documents\/([^/]+)\/open$/))) return json({ document_id: m[1], path: ".engineering/specs/x.md", editor: "code" });
    if ((m = p.match(/^\/documents\/([^/]+)$/))) {
      const all = Object.values(S.docs).flat();
      return json(documentDetail(decodeURIComponent(m[1]), all));
    }
    if (p === "/beads/search") return json({ query: q.get("q"), beads: [
      { id: "kraft-a4js", title: "Gate card findings list pushes the split off-screen", status: "open", issue_type: "bug" },
      { id: "kraft-qqz8", title: "Task 3 of 6 progress line on the board row", status: "in_progress", issue_type: "feature" },
    ] });

    /* settings */
    const st = S.settings;
    if (p === "/repos") {
      if (method === "GET") return json(opts.ngBoard && opts.ngBoard !== "empty" ? { repos: NG_REPOS, workspaces: NG_WORKSPACES } : { repos: st.repos });
      if (method === "DELETE") return { status: 204 };
      return json({ ...(st.repos[0] ?? {}), ...(req.json() ?? {}) });
    }
    if (p === "/repos/probe") return json({ path: req.json()?.path ?? "/tmp/x", name: "x", branch: "main", submodules: ["vendor/kraft-lite"], has_beads: true, beads_export_auto: false, beads_export_git_add: true, has_engineering: true, test_command: "uv run pytest -q", test_scopes: null, forge: "gitlab", project: "acme/x" });
    if (hs) {
      if (p === "/harnesses") return json(PROVIDER_STATUS);
      if (p === "/policy") return json({ maxima: { allowed_tools: ["git", "shell"] }, defaults: { escalation_grants: ["git-commit"] } });
      if (p === "/drafts") return json([{ area: "harnesses", key: "harnesses", files: ["harnesses.yaml", "policy.yaml"], changes: hs.view().result.changes.length, problems: hs.problems().length, updated_at: "2026-10-01T09:12:00Z" }]);
      if ((m = p.match(/^\/drafts\/harnesses\/harnesses(?:\/(undo|publish|ops|rebase|fragment))?$/))) {
        if (method === "DELETE") return { status: 204 };
        if (m[1] === "ops") {
          const ops: Record<string, unknown>[] = req.json()?.ops ?? [];
          hs.apply(ops);
          return json({ ...hs.view(), ops: ops.map((o) => ({ op: o.op })) });
        }
        if (m[1] === "publish") {
          const blocking = hs.problems();
          if (blocking.length) return json({ detail: `${blocking.length} problem(s) to fix before publishing`, problems: blocking }, 422);
          hs.publish();
          return json({ published: ["harnesses.yaml", "policy.yaml"], result: hs.view().result });
        }
        return json(hs.view());
      }
    }
    if (p === "/harnesses") return json([
      { id: "claude", label: "claude", executable: "claude", executable_found: true, efforts: ["low", "medium", "high", "xhigh", "max"], models: [], capabilities: {} },
      { id: "codex", label: "codex", executable: "codex", executable_found: false, efforts: [], models: [], capabilities: {} },
    ]);
    /* ux2-W15: the settings areas' drafts */
    if ((m = p.match(/^\/drafts\/(repos|policy|intake)\/\1(?:\/(undo|publish|ops|rebase|fragment|preview)|\/files\/(.+))?$/))) {
      const [status, body] = areas.handle(m[1], m[2], m[3], method, req.json() ?? {}, q) ?? [404, { detail: "no" }];
      return status === 204 ? { status: 204 } : json(body, status);
    }
    /* config drafts. Chains: the real answers above; ops apply the few the flows send; `stale` publishes to a 409. */
    if ((m = p.match(/^\/drafts\/chains\/([^/]+)(?:\/(undo|publish|ops|rebase|fragment)|\/files\/(.+))?$/))) {
      const [, key, action, file] = m;
      if (method === "DELETE") return { status: 204 };
      if (action === "publish") {
        if (key === "stale") return json(DRAFTS.stale409, 409);
        if (key === "broken" || key === "yaml-error") return json({ detail: "1 problem(s) to fix before publishing", problems: chainView(key).result.problems }, 422);
        return json({ published: [`chains/${key}.yaml`], result: { ...chainView(key).result, changes: [] } });
      }
      if (action === "fragment") return json({ path: q.get("path"), text: fragmentOf(chainView(key), String(q.get("path"))) });
      const view = chainView(key);
      if (file) view.files[file] = req.json()?.text ?? "";
      if (action === "ops") {
        if (key === "yaml-error") return json({ detail: "fix the YAML first" }, 409);
        const ops: Record<string, unknown>[] = req.json()?.ops ?? [];
        // change_base's preview: what the new base keeps and drops (W9's {kept, dropped}).
        if (q.get("preview")) return json({ ...view, ops: ops.map((o) => ({ op: o.op, result: o.op === "change_base" ? { kept: ["skippable"], dropped: [{ key: "steps", why: `${o.base} has no step tests` }] } : undefined })) });
        return json({ ...applyOps(view, ops), ops: ops.map((o) => ({ op: o.op })) });
      }
      return json(view);
    }
    /* ux2-W12: the Library's draft, kept for the test's life */
    if (lib && (m = p.match(/^\/drafts\/library\/([^/]+)(?:\/(undo|publish|ops|rebase|fragment)|\/files\/(.+))?$/))) {
      const [, , action, file] = m;
      if (method === "DELETE") return { status: 204 };
      if (action === "fragment") return json({ path: q.get("path"), text: lib.fragment(String(q.get("path"))) });
      if (action === "publish") {
        if (lib.view().result.problems.length) return json({ detail: `${lib.view().result.problems.length} problem(s) to fix before publishing`, problems: lib.view().result.problems }, 422);
        return json({ published: ["library.yaml"], result: { ...lib.view().result, changes: [] } });
      }
      if (file) lib.text = req.json()?.text ?? "";
      if (action === "ops") {
        const ops: Record<string, unknown>[] = req.json()?.ops ?? [];
        const answers = [];
        for (const o of ops) {
          const refused = lib.apply(o);
          if (typeof refused === "string") return json({ detail: refused, op: answers.length }, 422);
          answers.push({ op: o.op, ...(refused ? { result: refused } : {}) });
        }
        return json({ ...lib.view(), ops: answers });
      }
      return json(lib.view());
    }
    /* the library draft: one static answer */
    if ((m = p.match(/^\/drafts\/(library)\/([^/]+)(?:\/(undo|publish|ops|rebase|fragment)|\/files\/(.+))?$/))) {
      const [, area, key, action, file] = m;
      const name = "library.yaml";
      const text = file ? req.json()?.text : "tasks: {}\n";
      const result = {
        model: { [name]: { tasks: {} } }, resolved: null, warnings: [], sources: {},
        policy_values: { auto_escalate_delay_s: 0, auto_review_attempts: 1 },
        impact: { chains: ["default", "quick-task"], repos: ["/Users/me/code/kraft"] },
        problems: [],
        changes: [
          { path: "tasks.implementer", kind: "change", summary: "prompt", fields: ["prompt"], reaches: ["default", "quick-task"] },
          { path: "steering.project-standards", kind: "change", summary: "instructions", fields: ["instructions"], reaches: ["default"] },
        ],
      };
      if (method === "DELETE") return { status: 204 };
      if (action === "fragment") return json({ path: url.searchParams.get("path"), text: "model: opus\nprompt: Implement the change.\n" });
      if (action === "publish") return json({ published: [name], result: { ...result, changes: [] } });
      const view = { area, key, draft: true, files: { [name]: text }, base: { [name]: "9f2c".padEnd(64, "0") }, updated_at: "2026-10-01T09:12:00Z", result };
      if (action === "ops") return json({ ...view, ops: (req.json()?.ops ?? []).map((o: { op: string }) => ({ op: o.op })) });
      return json(view);
    }
    /* the agent task's choices in the Chains editor (real answers, draftViews.json) */
    if (p === "/harnesses/profiles" && method === "GET") return json(DRAFTS.harnesses);
    if (p === "/harnesses/providers") return json(DRAFTS.providers);
    if (p === "/drafts") return json([...areas.list(), ...["default", "broken", "yaml-error", "stale"].map((key) => ({ area: "chains", key, files: [`chains/${key}.yaml`], changes: 1, problems: key === "broken" ? 1 : 0, updated_at: "2026-10-01T09:12:00Z" }))]);
    if (p === "/templates/chains") return json(opts.ngBoard ? NG_CHAINS : st.templates);
    if (p === "/templates/parse") return json({ nodes: st.templates[0]?.nodes ?? [], error: null });
    if ((m = p.match(/^\/templates\/([^/]+)\/validate$/))) return json({ id: m[1], valid: true, error: null, unresolved: [] });
    /* the steering preview (B23): one agent task's launch context, section by section */
    if (p === "/templates/steering/preview") return json({ sections: [
      { kind: "contract", source: "kraft", text: `You are working on a Kraft work item.\nTitle: <title>\nTask: <task instruction>\nRepo: /Users/me/code/kraft\nWork item: <work item id>\nNode: ${q.get("task")?.split(".")[0] ?? "spec"}\nHook point: ${q.get("task") ?? ""}\nWorker session: <session id>\n` },
      { kind: "document", source: "spec", text: "\n\nWrite the spec to .engineering/specs/<work item id>.md." },
      { kind: "skill", source: "kraft:spec", text: "\n\n## Method\n\nUse the kraft:spec skill." },
      { kind: "steering", source: "repo:never-signal-processes-you-didnt-start", text: "\n\n## Project standards\n\nNever signal a process you did not start." },
      { kind: "steering", source: "task:project-standards", text: "\n\nKeep changes focused. Run the relevant checks before finishing.\n" },
    ] });
    if ((m = p.match(/^\/templates\/chains\/([^/]+)\/resolved$/))) {
      const tpl = st.templates.find((x) => x.id === decodeURIComponent(m![1]));
      if (!tpl) return json({ detail: `unknown chain template ${JSON.stringify(m[1])}` }, 404);
      return json({ id: tpl.id, chain: { nodes: tpl.nodes }, task_paths: [], steering: {}, nodes: tpl.nodes });
    }
    if ((m = p.match(/^\/templates\/chains\/([^/]+)$/))) {
      // For the Chains editor, `default` is the real shipped file, the draft views' published side.
      // The shipped Settings pages keep the scenario's chain (settings-chains/default reads this route).
      if (m[1] === "default") return json(DRAFTS.published);
      const tpl = st.templates.find((x) => x.id === decodeURIComponent(m![1])) ?? st.templates[0];
      if (!tpl) return json({ detail: "template not found" }, 404);
      // The real route returns the file's text, which is what the Chains editor shows.
      return json({ id: tpl.id, file: `templates/chains/${tpl.id}.yaml`, text: chainYaml(tpl.nodes), chain: { nodes: tpl.nodes }, nodes: tpl.nodes });
    }
    // The Library screen (where Settings > Steering redirects): the scenario's hooks as its tasks.
    // The editors' pickers read the real shipped library; the shipped Library page keeps the scenario's.
    if (p === "/templates/library" && method === "GET") return json(DRAFTS.library);
    if (p === "/templates/library" && method === "GET") {
      const hooks = Object.entries(st.hooks);
      const components = hooks.map(([name, definition]) => ({
        id: `tasks.${name}`, kind: "tasks", name, definition, issues: [],
        used_by: st.templates.filter((t) => t.nodes.some((n: any) => n.tasks?.includes(name))).map((t) => t.id),
        used_by_paths: st.templates.flatMap((t) => t.nodes.filter((n: any) => n.tasks?.includes(name)).map((n: any) => ({ chain: t.id, path: `${n.id}.main.${name}`, overrides: false }))),
      }));
      return json({ file: "templates/library.yaml", text: `tasks:\n${hooks.map(([k, v]) => `  ${k}: ${JSON.stringify(v)}`).join("\n")}\n`, components });
    }
    if (p === "/registry") return json({ hooks: st.hooks, invalid_templates: {} });
    if ((m = p.match(/^\/registry\/([^/]+)\/runs$/))) return json({ runs: Object.values(S.bundles).slice(0, 8).map((b, i) => ({ work_item_id: b.item.id, node_id: b.item.current_node_id ?? "verify", round: i % 3, status: ["done", "failed", "done", "capped_out"][i % 4], wall_ms: 120_000 + i * 40_000, created_at: b.item.updated_at })) });
    if (p === "/policy") return json(opts.areas ? { ...st.policy, max_concurrent: 5, active_count: areas.activeCount() } : st.policy);
    // PUT merges over the file, as the real route does since B30.
    if (p === "/theme") return json(method === "PUT" ? Object.assign(st.theme, req.json(), { derived: false }) : st.theme);
    if (p === "/steering") return json(st.steering);
    if ((m = p.match(/^\/steering\/([^/]+)$/))) return method === "DELETE" ? json({ deleted: m[1] }) : json(st.steeringBody(decodeURIComponent(m[1])));
    if (p === "/intake/checks" && opts.areas) return json(areas.checks());
    if (p === "/intake/checks") return json([
      { id: 3, at: new Date(Date.now() - 60_000).toISOString(), ready: 2, started: ["w-2"], skipped: [{ bead: "B-9", reason: "max_concurrent" }] },
      { id: 2, at: new Date(Date.now() - 360_000).toISOString(), ready: 0, started: [], skipped: [] },
    ]);
    if (p === "/apply" || p === "/apply/reload") {
      return json({ restart: [], reload: [], managed: true });
    }
    if (p === "/apply/restart") { restarted = 1; return { status: 202, contentType: "application/json", body: JSON.stringify({ restarting: true }) }; }
    if (p === "/update" || p === "/update/check") {
      const at = new Date(Date.now() - 3_600_000).toISOString();
      const v = opts.version;
      return json({ installed: v ?? "1.4.0", latest: v ? `v${v}` : "v1.5.0", channel: "stable", python: "3.14", behind: !v, checked_at: at });
    }
    if (p === "/intake") return json(st.intake);
    if (p === "/access") return json(st.access);
    if (p === "/notify") return json(st.notify);
    if (p === "/notify/test") return json({ at: new Date().toISOString(), status: 200, ms: 388, error: null });
    if (p === "/sessions") return json(st.sessions);
    if ((m = p.match(/^\/sessions\/[^/]+$/))) return { status: 204 };
    if (p === "/analytics") return json(S.analytics);
    if (p === "/storage") return json(storageFor([...S.ngBoard, ...S.ngArchived]));
    if (p === "/storage/preview" && method === "POST") {
      const usage = storageFor([...S.ngBoard, ...S.ngArchived]);
      const picked = usage.items.filter((i) => ((req.json() ?? {}).ids ?? []).includes(i.id));
      const freed = picked.reduce((n, i) => n + i.bytes, 0);
      return json({ freed_bytes: freed, used_after_bytes: usage.used_bytes - freed, state_after: "ok",
        items: picked.map((i, k) => ({ id: i.id, title: i.title, bytes: i.bytes, archivable: true, refusal: null, uncommitted_files: k === 0 ? 2 : 0, unpushed_commits: 0, branch_kept: false })) });
    }
    if (p === "/editors") return json({ available: ["code", "cursor"], system: true, default: null });
    if (p === "/login") return json({ ok: true });
    if (p === "/logout") return { status: 204 };

    return json({ detail: `mock: unhandled ${method} ${p}` }, 404);
  };
  // The demo's handle on its motion, to pause it while the tab is hidden; null without `motion`.
  return Object.assign(serve, { motion: opts.motion ? motion(S, T, opts.motion === true ? {} : opts.motion) : null });
}

const GB = 1024 ** 3;
/** `GET /storage` for the demo's own items: each worktree 0.4–2G, the finished ones what a clean-up would free, under a 40G quota. */
function storageFor(items: any[]) {
  const rows = items.map((i, k) => {
    const ended = i.status === "completed" || i.status === "abandoned";
    return { id: i.id, title: i.title, status: i.status, archived: !!i.archived_at, bytes: i.archived_at ? 0 : ((k * 7) % 5 + 1) * 0.4 * GB, documents_bytes: 3 * 1024 ** 2, updated_at: i.updated_at, reclaimable: ended && !i.archived_at };
  }).sort((a, b) => b.bytes - a.bytes);
  const worktrees = rows.reduce((n, r) => n + r.bytes, 0);
  const categories = { worktrees, sandboxes: 0, documents: rows.length * 3 * 1024 ** 2, logs: 180 * 1024 ** 2, results: 12 * 1024 ** 2, databases: 40 * 1024 ** 2, attachments: 0, other: 0 };
  const used = Object.values(categories).reduce((n, b) => n + b, 0);
  return { measured_at: new Date(Date.now() - 120_000).toISOString(), state: "ok", used_bytes: used, quota_bytes: 40 * GB, limit_bytes: 50 * GB,
    reclaimable_bytes: rows.filter((r) => r.reclaimable).reduce((n, r) => n + r.bytes, 0), categories, items: rows, orphans: [] };
}
