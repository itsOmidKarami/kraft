import type { Page, Route } from "@playwright/test";
import { readFileSync } from "node:fs";
import { NG_CHAINS, NG_REPOS, NG_WORKSPACES, ngDryRun } from "./ngBoard";
import { artifactFor, compareFor, diffFor, fixTargetFor, documentDetail, searchFor, type Scenario } from "./fixtures";
import { NG_NOW, ngThreads } from "./ngItems";

export interface MockOptions {
  /** Every call except /health answers 401 → the Login screen. */
  locked?: boolean;
  /** What POST /login answers while locked: 200, a 401, or a 429 with Retry-After (default 200). */
  login?: "ok" | "wrong" | "locked";
  /** ux2-W6: GET /work-items answers the /ng board's fixtures (`S.ngBoard`, `S.ngArchived`); "empty" answers none. */
  ngBoard?: boolean | "empty";
  /** ux2-W6 board states. `loading`: boot's list read fails, every later one never answers.
   *  `offline`: boot's read answers, every later one fails to connect, and the event socket closes. */
  boardState?: "loading" | "offline";
  /** Bead ids whose bulk action fails as if someone paused it a moment before (a partial answer). */
  bulkFail?: string[];
  /** ux2-W11: the running /ng item's chain draft. Unset or `none`: no draft (the + seam's menu reads the real library `/ng` gets). `applied`: none, but its applied draft is in the events. */
  itemDraft?: "none" | "changes" | "problems" | "passed" | "applied";
}

/** The ops each `itemDraft` state starts from (the running item stands on `verification`). */
const ITEM_DRAFT_SEEDS: Record<string, any[]> = {
  changes: [
    { op: "override", path: "merge_request.open.open_draft", task_config: { model: "opus" } },
    { op: "override", path: "mr_checks", policy: { time_cap_minutes: 45 } },
    { op: "add_node", after: "work_brief", node: { id: "security_scan", extends: "security_scan" } },
  ],
  problems: [{ op: "override", path: "merge_request.open.open_draft", task_config: { command: "make" } }],
  passed: [
    { op: "add_node", after: "implementation", node: { id: "security_scan", extends: "security_scan" } },
    { op: "override", path: "verification.test.unit_tests", task_config: { model: "opus" } },
  ],
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
const DRAFTS = JSON.parse(readFileSync(new URL("./draftViews.json", import.meta.url), "utf8"));
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
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
/** Block YAML of a mapping, for the fragment route's sweep answer only (the server writes the real one). */
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
/** An op answered as the server would, for the ops the sweep's flows send. */
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
  view.draft = true;
  return view;
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

/**
 * Serve the whole /api surface from the scenario. Mutating verbs return a
 * plausible 200 so a composer's Submit never crashes a shot, but nothing
 * changes — the sweep is about how the UI looks, not what it does.
 */
export async function installMocks(page: Page, S: Scenario, opts: MockOptions = {}) {
  // Every sweep page is mocked here, before it navigates: fixed time keeps the app's relative durations ("17d 10h") off the wall clock. Timers still run; a case that needs another instant sets its own after.
  await page.clock.setFixedTime(new Date(NG_NOW));
  // The live-events socket: accept and stay silent so the shell reads "live".
  await page.routeWebSocket(/\/api\/ws\/events/, (ws) => { if (opts.boardState === "offline") ws.close(); });
  let listReads = 0;
  const itemDrafts = new Map<string, any[]>();
  // ux2-W11: an applied draft already on the running item, as its event.
  if (opts.itemDraft === "applied" && S.ng?.running) {
    const b = S.bundles[S.ng.running];
    b?.events.push({ seq: Math.max(0, ...b.events.map((e: any) => e.seq)) + 1, work_item_id: S.ng.running, type: "chain_revised", payload: { gate: null, changes: ITEM_DRAFT_SEEDS.changes.slice(0, 2), diff: [], source: "draft" }, node_id: null, created_at: NG_NOW });
  }

  const viewedMarks = new Set<string>();
  // ux2-W8: review threads per item, seeded for a needs-gate /ng item on first read.
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
  let seq = 0;
  await page.route(/\/api\//, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname.replace(/^.*?\/api/, "");
    const method = req.method();
    const q = url.searchParams;

    if (p === "/health") return json(route, S.settings.health);
    if (opts.locked) {
      if (p === "/login" && method === "POST") {
        if (opts.login === "wrong") return json(route, { detail: "Wrong password." }, 401);
        if (opts.login === "locked") return route.fulfill({ status: 429, contentType: "application/json", headers: { "Retry-After": "125" }, body: JSON.stringify({ detail: "Too many failed logins. Try again later." }) });
        return json(route, { ok: true });
      }
      return json(route, { detail: "unauthenticated" }, 401);
    }

    let m: RegExpMatchArray | null;

    /* budget */
    if (p === "/budget/today") return json(route, { spent_usd: 8.3, cap_usd: 50 });

    /* work items */
    if (p === "/work-items" && method === "GET" && opts.boardState && q.get("archived") !== "true") {
      const n = ++listReads;
      if (opts.boardState === "offline" && n > 1) return route.abort("connectionrefused");
      if (opts.boardState === "loading") return n === 1 ? route.abort("connectionrefused") : undefined; // later reads never answer
    }
    if (p === "/work-items" && method === "GET") {
      const arch = q.get("archived") === "true";
      const list = opts.ngBoard ? (opts.ngBoard === "empty" ? [] : arch ? S.ngArchived : S.ngBoard) : arch ? S.archived : S.items;
      return json(route, { items: list, cursor: 4242 });
    }
    if (p === "/work-items" && method === "POST" && q.get("dry_run") && opts.ngBoard) {
      const r = ngDryRun(req.postDataJSON() ?? {});
      return json(route, r.body, r.status);
    }
    // ux2-W6: a create from the /ng composer lands on the board's never-started row, which has a detail for the peek.
    if (p === "/work-items" && method === "POST" && !q.get("dry_run") && opts.ngBoard) {
      return json(route, { id: S.ngBoard.find((i) => i.bead_id === "kraft-f5d3")?.id ?? S.ngBoard[0]?.id, status: "paused" }, 201);
    }
    if (p === "/work-items" && method === "POST" && q.get("dry_run")) {
      const nodes = S.items[0]?.chain_definition?.nodes ?? [];
      return json(route, {
        dry_run: true,
        nodes,
        skipped: [],
        gates: nodes.filter((n: any) => n.gate_after === n.id).map((n: any) => n.id),
        caps: { budget_usd: 5, budget_source: "policy", daily_usd: 50, nodes: {} },
      });
    }
    if (p === "/work-items" && method === "POST") return json(route, { id: S.items[0]?.id ?? "00000000000000000000000000000000" });
    if (method === "POST" && p === "/work-items/bulk") {
      // B9: each id through its own route's door, with that door's words (lifecycle.py).
      const { action, ids = [], reason } = req.postDataJSON() ?? {};
      if (action === "cancel" && !String(reason ?? "").trim()) return json(route, { detail: "reason: cancel needs a non-blank reason" }, 422);
      const known = [...S.ngBoard, ...S.ngArchived, ...S.items, ...S.archived];
      const fail = new Set((opts.bulkFail ?? []).map((bead) => known.find((i) => i.bead_id === bead)?.id));
      return json(route, { results: (ids as string[]).map((id) => {
        const i = known.find((x) => x.id === id);
        const status = i?.status ?? "active";
        const ended = status === "completed" || status === "abandoned";
        const error = fail.has(id) ? `work item is paused, not running`
          : action === "pause" && !["active", "waiting"].includes(status) ? `work item is ${status}, not running`
          : action === "cancel" && ended ? `work item is ${status}; its chain does not run again`
          : action === "archive" && !ended ? "only a completed or abandoned item can be archived"
          : action === "restore" && !i?.archived_at ? "work item is not archived" : null;
        return error ? { id, ok: false, status, error } : { id, ok: true, status: action === "pause" ? "paused" : action === "cancel" ? "abandoned" : status };
      }) });
    }
    if ((m = p.match(/^\/work-items\/([^/]+)$/))) {
      const b = S.bundles[m[1]];
      if (!b) return json(route, { detail: "work item not found" }, 404);
      if (method === "PATCH") return json(route, { id: m[1], ...(req.postDataJSON() ?? {}) });
      return json(route, { ...b.item, worker_sessions: b.sessions });
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/events$/))) {
      const b = S.bundles[m[1]];
      const all = b?.events ?? [];
      const limit = q.get("limit") ? Number(q.get("limit")) : undefined;
      const before = q.get("before_seq") ? Number(q.get("before_seq")) : undefined;
      if (before !== undefined) {
        const page = all.filter((e) => e.seq < before);
        return json(route, page.slice(-(limit ?? 100)));
      }
      const after = Number(q.get("after_seq") ?? 0);
      const page = all.filter((e) => e.seq > after);
      return json(route, limit !== undefined ? page.slice(0, limit) : page);
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/documents$/))) {
      return json(route, { work_item_id: m[1], documents: S.docs[m[1]] ?? [] });
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/diff$/))) return json(route, diffFor(m[1], S.variant));
    if ((m = p.match(/^\/work-items\/([^/]+)\/compare$/))) {
      return json(route, compareFor(m[1], S.variant, ["1", "true"].includes(q.get("ignore_whitespace") ?? ""), viewedMarks));
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/viewed$/)) && (method === "PUT" || method === "DELETE")) {
      const file = q.get("file") ?? "";
      viewedMarks[method === "PUT" ? "add" : "delete"](`${m[1]}|${file}`);
      return json(route, { file, to: q.get("to") ?? "latest", viewed: method === "PUT" });
    }
    /* ux2-W8: review threads and comments, kept for the test's life */
    if ((m = p.match(/^\/work-items\/([^/]+)\/threads$/))) {
      const list = threadsOf(m[1]);
      if (method === "GET") return json(route, list);
      const b = req.postDataJSON() ?? {};
      const id = `th-new${seq++}`;
      const t = { id, work_item_id: m[1], gate: S.bundles[m[1]]?.item.pending_gate ?? null, node_id: b.node_id ?? null, file_path: b.file_path ?? null, side: b.side ?? null, start_line: b.start_line ?? null, end_line: b.end_line ?? null, anchor_sha: b.anchor_sha ?? "9c8d7e6", label: b.label ?? null, state: "open", resolved_at: null, created_at: "2026-09-13T10:09:00.000Z", draft: true,
        comments: [{ id: `c-new${seq++}`, thread_id: id, review_id: null, author: "you", attempt: null, body: b.body, suggestion: b.suggestion ?? null, claim: null, created_at: "2026-09-13T10:09:00.000Z", draft: true }] };
      list.push(t);
      return json(route, t, 201);
    }
    if ((m = p.match(/^\/threads\/([^/]+)(?:\/(comments|resolve|reopen))?$/))) {
      const t = findThread(m[1]);
      if (!t) return json(route, { detail: `unknown thread ${m[1]}` }, 404);
      const b = method === "GET" ? {} : req.postDataJSON() ?? {};
      if (m[2] === "comments") {
        const c = { id: `c-new${seq++}`, thread_id: t.id, review_id: null, author: "you", attempt: null, body: b.body, suggestion: b.suggestion ?? null, claim: null, created_at: "2026-09-13T10:09:00.000Z", draft: true };
        t.comments.push(c);
        return json(route, c, 201);
      }
      if (m[2]) return json(route, Object.assign(t, { state: m[2] === "resolve" ? "resolved" : "open" }));
      if (method === "DELETE") { threads[t.work_item_id] = threads[t.work_item_id].filter((x) => x !== t); return route.fulfill({ status: 204 }); }
      Object.assign(t.comments[0], { body: b.body ?? t.comments[0].body, suggestion: "suggestion" in b ? b.suggestion : t.comments[0].suggestion });
      if ("label" in b) t.label = b.label;
      return json(route, t);
    }
    // A submitted review publishes the drafts; request_changes and approve move the item as the gate call would.
    if (method === "POST" && (m = p.match(/^\/work-items\/([^/]+)(?:\/gates\/([^/]+))?\/review$/))) {
      const b = S.bundles[m[1]];
      if (!b) return json(route, { detail: "work item not found" }, 404);
      const it = b.item;
      const { outcome } = req.postDataJSON() ?? {};
      if (m[2] && it.pending_gate !== m[2]) return json(route, { detail: `gate '${m[2]}' is not pending` }, 409);
      const list = threadsOf(m[1]);
      if (outcome === "approve" && list.some((t) => t.label === "must_fix" && t.state !== "resolved"))
        return json(route, { detail: `must-fix review threads are not resolved: ${list.filter((t) => t.label === "must_fix" && t.state !== "resolved").map((t) => t.id).join(", ")}` }, 409);
      for (const t of list) { t.draft = false; for (const c of t.comments) if (c.draft) Object.assign(c, { draft: false, review_id: "r2" }); }
      const gate = m[2] ?? null;
      if (outcome === "comment") return json(route, { review_id: "r2", outcome, gate, reply_agent: !!gate });
      const target = outcome === "approve" ? null : it.fix_target?.node ?? fixTargetFor(null).node;
      Object.assign(it, { status: "active", display_status: "running", stop: null, pending_gate: null, gate_artifact: null, fix_target: null, attempts: [] });
      if (target) it.current_node_id = target;
      if (gate) return json(route, { id: it.id, status: "active" });
      return json(route, { review_id: "r2", outcome, gate: null, target, target_reason: "current node", action: "rerun" });
    }
    if ((m = p.match(/^\/comments\/([^/]+)$/))) {
      const c = findComment(m[1]);
      if (!c) return json(route, { detail: `unknown comment ${m[1]}` }, 404);
      const t = findThread(c.thread_id);
      if (method === "DELETE") { t.comments = t.comments.filter((x: any) => x !== c); return route.fulfill({ status: 204 }); }
      return json(route, Object.assign(c, { body: req.postDataJSON()?.body ?? c.body }));
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/fix-target$/))) {
      return json(route, fixTargetFor(S.bundles[m[1]]?.item.pending_gate ?? null, q.get("node") ?? undefined));
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/artifact$/))) {
      const b = S.bundles[m[1]];
      return b ? json(route, artifactFor(b.item, S.variant)) : json(route, { detail: "no artifact" }, 404);
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/cancel-preview$/))) {
      const b = S.bundles[m[1]];
      if (!b) return json(route, { detail: "work item not found" }, 404);
      const it = b.item;
      return json(route, {
        running: it.current_node_id ? { node: it.current_node_id, task: it.current_node_id, attempt: 1 } : null,
        kept: { branch: `kraft/${it.id}`, worktree: `/tmp/kraft/worktrees/${it.id}`, findings: (it.deferred_findings ?? []).length, threads: 0 },
        mr: it.mr_ref ? { ref: it.mr_ref.number, url: it.mr_ref.url, state: "open" } : null,
        spend: { spent_usd: it.budget_cap?.spent_usd ?? 0, cap_usd: it.budget_cap?.cap_usd ?? null },
      });
    }
    if (method === "POST" && (m = p.match(/^\/work-items\/([^/]+)\/duplicate$/))) {
      return json(route, { id: `${m[1]}-dup`, status: "paused" }, 201);
    }
    /* the item chain draft (B15, ux2-W11): none unless `opts.itemDraft` seeds the running item's; then a small stateful server */
    if ((m = p.match(/^\/work-items\/([^/]+)\/draft(\/apply)?$/))) {
      const b = S.bundles[m[1]];
      if (!b) return json(route, { detail: "work item not found" }, 404);
      const cur = b.item.current_node_id ?? null;
      const stored = (): any[] => {
        if (!itemDrafts.has(m![1])) itemDrafts.set(m![1], opts.itemDraft && opts.itemDraft !== "applied" && opts.itemDraft !== "none" && m![1] === S.ng.running ? clone(ITEM_DRAFT_SEEDS[opts.itemDraft]) : []);
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
      if (method === "DELETE") { const had = stored().length > 0; itemDrafts.set(m[1], []); return had ? route.fulfill({ status: 204 }) : json(route, { detail: "this work item has no draft" }, 404); }
      if (method === "PUT") { itemDrafts.set(m[1], req.postDataJSON()?.ops ?? []); return json(route, answer(stored())); }
      if (m[2]) {
        const ops = stored();
        if (!ops.length) return json(route, { detail: "this work item has no draft" }, 404);
        const v = answer(ops);
        const passed = v.ops.flatMap((op: any, i: number) => (op.passed ? [i] : []));
        if (passed.length) return json(route, { detail: "the item has moved past some of this draft's ops; remove them or move them after the current node", passed }, 409);
        if (v.problems.length) return json(route, { detail: `${v.problems.length} problem(s) to fix before applying`, problems: v.problems }, 422);
        b.item.chain_definition.nodes = v.nodes;
        b.events.push({ seq: Math.max(0, ...b.events.map((e: any) => e.seq)) + 1, work_item_id: m[1], type: "chain_revised", payload: { gate: null, changes: ops, diff: [], source: "draft" }, node_id: null, created_at: NG_NOW });
        itemDrafts.set(m[1], []);
        return json(route, answer([]));
      }
      return json(route, answer(stored()));
    }
    // The four mutations post-action frames need (W6.3): the scenario changes
    // so the next GET shows the state the action produced. Everything else
    // below stays a static 200.
    if (method === "POST" && (m = p.match(/^\/work-items\/([^/]+)\/(pause|resume|gates\/[^/]+\/(?:approve|reject))$/))) {
      const b = S.bundles[m[1]];
      if (b) {
        const it = b.item;
        const body = req.postDataJSON() ?? {};
        if (m[2].endsWith("/approve")) Object.assign(it, { status: "active", pending_gate: null, gate_artifact: null });
        else if (m[2].endsWith("/reject")) {
          const node = it.chain_definition.nodes.find((n: any) => n.id === it.current_node_id);
          Object.assign(it, { status: "active", pending_gate: null, gate_artifact: null, current_node_id: node?.reject_to ?? it.current_node_id });
        } else if (m[2] === "pause") it.status = "paused";
        else {
          it.status = "active";
          if (body.steer) {
            const last = b.sessions.at(-1) ?? {};
            b.sessions.push({ ...last, id: `${last.id ?? "0".repeat(32)}`.slice(0, 31) + String(b.sessions.length % 10), node_id: it.current_node_id, status: "pending", attempt: (last.attempt ?? 0) + 1, created_at: new Date().toISOString(), started_at: null, exited_at: null, tokens_in: null, tokens_out: null, cost_usd: null, wall_ms: null, session_summary_ref: null });
          }
        }
        it.updated_at = new Date().toISOString();
      }
    }
    // ux2-W5: cancel and complete end the item (the /ng page reads display_status); reassign and
    // keep-waiting are B5's routes (R2), answered here only so the worker-lost fixture's card can be driven.
    if (method === "POST" && (m = p.match(/^\/work-items\/([^/]+)\/(cancel|complete)$/))) {
      const b = S.bundles[m[1]];
      if (b) Object.assign(b.item, m[2] === "cancel" ? { status: "abandoned", display_status: "cancelled", stop: null } : { status: "completed", display_status: "done", stop: null });
      return json(route, { id: m[1], status: m[2] === "cancel" ? "abandoned" : "completed" });
    }
    if (method === "POST" && (m = p.match(/^\/work-items\/([^/]+)\/(reassign|keep-waiting)$/))) return json(route, { id: m[1], status: "waiting" });
    if ((m = p.match(/^\/work-items\/([^/]+)\/(pause|resume|retry|reopen-mr|skip|abandon|archive|restore|escalate|open-worktree|budget\/raise|escalate\/stop|gates\/[^/]+\/(approve|reject))$/))) {
      return json(route, { id: m[1], status: "active", node_id: "implement", loop: "verify_fix_loop", steer: null, path: "/tmp", editor: "code" });
    }

    /* logs: jsonl, plain text, SSE tail */
    if ((m = p.match(/^\/worker-sessions\/([^/]+)\/log$/))) {
      const sid = m[1];
      const bundle = Object.values(S.bundles).find((b) => b.logs[sid]);
      const lines = bundle?.logs[sid] ?? [];
      const status = bundle?.sessions.find((s) => s.id === sid)?.status ?? "done";
      if (q.get("follow") === "1") {
        const body = lines.slice(-5).map((l) => `data: ${JSON.stringify(l)}\n\n`).join("") + "event: end\ndata: {}\n\n";
        return route.fulfill({ status: 200, contentType: "text/event-stream", body });
      }
      if (q.get("format") === "jsonl") return json(route, { session_id: sid, status, lines });
      return route.fulfill({ status: 200, contentType: "text/plain", body: lines.map((l) => l.text).join("\n") });
    }

    /* documents & search */
    if (p === "/search") {
      const all = Object.values(S.docs).flat();
      return json(route, searchFor(q.get("q") ?? "", all, S.variant));
    }
    if ((m = p.match(/^\/documents\/([^/]+)\/open$/))) return json(route, { document_id: m[1], path: ".engineering/specs/x.md", editor: "code" });
    if ((m = p.match(/^\/documents\/([^/]+)$/))) {
      const all = Object.values(S.docs).flat();
      return json(route, documentDetail(decodeURIComponent(m[1]), all));
    }
    if (p === "/beads/search") return json(route, { query: q.get("q"), beads: [
      { id: "kraft-a4js", title: "Gate card findings list pushes the split off-screen", status: "open", issue_type: "bug" },
      { id: "kraft-qqz8", title: "Task 3 of 6 progress line on the board row", status: "in_progress", issue_type: "feature" },
    ] });

    /* settings */
    const st = S.settings;
    if (p === "/repos") {
      if (method === "GET") return json(route, opts.ngBoard && opts.ngBoard !== "empty" ? { repos: NG_REPOS, workspaces: NG_WORKSPACES } : { repos: st.repos });
      if (method === "DELETE") return route.fulfill({ status: 204 });
      return json(route, { ...(st.repos[0] ?? {}), ...(req.postDataJSON() ?? {}) });
    }
    if (p === "/repos/probe") return json(route, { path: req.postDataJSON()?.path ?? "/tmp/x", name: "x", branch: "main", submodules: ["vendor/kraft-lite"], has_beads: true, beads_export_auto: false, beads_export_git_add: true, has_engineering: true, test_command: "uv run pytest -q", test_scopes: null, forge: "gitlab", project: "acme/x" });
    if (p === "/harnesses") return json(route, [
      { id: "claude", label: "claude", executable: "claude", executable_found: true, efforts: ["low", "medium", "high", "xhigh", "max"], models: [], capabilities: {} },
      { id: "codex", label: "codex", executable: "codex", executable_found: false, efforts: [], models: [], capabilities: {} },
    ]);
    /* config drafts. Chains: the real answers above; ops apply the few the flows send; `stale` publishes to a 409. */
    if ((m = p.match(/^\/drafts\/chains\/([^/]+)(?:\/(undo|publish|ops|rebase|fragment)|\/files\/(.+))?$/))) {
      const [, key, action, file] = m;
      if (method === "DELETE") return route.fulfill({ status: 204 });
      if (action === "publish") {
        if (key === "stale") return json(route, DRAFTS.stale409, 409);
        if (key === "broken" || key === "yaml-error") return json(route, { detail: "1 problem(s) to fix before publishing", problems: chainView(key).result.problems }, 422);
        return json(route, { published: [`chains/${key}.yaml`], result: { ...chainView(key).result, changes: [] } });
      }
      if (action === "fragment") return json(route, { path: q.get("path"), text: fragmentOf(chainView(key), String(q.get("path"))) });
      const view = chainView(key);
      if (file) view.files[file] = req.postDataJSON()?.text ?? "";
      if (action === "ops") {
        if (key === "yaml-error") return json(route, { detail: "fix the YAML first" }, 409);
        const ops: Record<string, unknown>[] = req.postDataJSON()?.ops ?? [];
        // change_base's preview: what the new base keeps and drops (W9's {kept, dropped}).
        if (q.get("preview")) return json(route, { ...view, ops: ops.map((o) => ({ op: o.op, result: o.op === "change_base" ? { kept: ["skippable"], dropped: [{ key: "steps", why: `${o.base} has no step tests` }] } : undefined })) });
        return json(route, { ...applyOps(view, ops), ops: ops.map((o) => ({ op: o.op })) });
      }
      return json(route, view);
    }
    /* the library draft: one static answer */
    if ((m = p.match(/^\/drafts\/(library)\/([^/]+)(?:\/(undo|publish|ops|rebase|fragment)|\/files\/(.+))?$/))) {
      const [, area, key, action, file] = m;
      const name = "library.yaml";
      const text = file ? req.postDataJSON()?.text : "tasks: {}\n";
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
      if (method === "DELETE") return route.fulfill({ status: 204 });
      if (action === "fragment") return json(route, { path: new URL(req.url()).searchParams.get("path"), text: "model: opus\nprompt: Implement the change.\n" });
      if (action === "publish") return json(route, { published: [name], result: { ...result, changes: [] } });
      const view = { area, key, draft: true, files: { [name]: text }, base: { [name]: "9f2c".padEnd(64, "0") }, updated_at: "2026-10-01T09:12:00Z", result };
      if (action === "ops") return json(route, { ...view, ops: (req.postDataJSON()?.ops ?? []).map((o: { op: string }) => ({ op: o.op })) });
      return json(route, view);
    }
    /* the agent task's choices in the Chains editor (real answers, sweep/draftViews.json) */
    if (p === "/harnesses/profiles" && method === "GET") return json(route, DRAFTS.harnesses);
    if (p === "/harnesses/providers") return json(route, DRAFTS.providers);
    if (p === "/drafts") return json(route, ["default", "broken", "yaml-error", "stale"].map((key) => ({ area: "chains", key, files: [`chains/${key}.yaml`], changes: 1, problems: key === "broken" ? 1 : 0, updated_at: "2026-10-01T09:12:00Z" })));
    if (p === "/templates/chains") return json(route, opts.ngBoard ? NG_CHAINS : st.templates);
    if (p === "/templates/parse") return json(route, { nodes: st.templates[0]?.nodes ?? [], error: null });
    if ((m = p.match(/^\/templates\/([^/]+)\/validate$/))) return json(route, { id: m[1], valid: true, error: null, unresolved: [] });
    /* the steering preview (B23): one agent task's launch context, section by section */
    if (p === "/templates/steering/preview") return json(route, { sections: [
      { kind: "contract", source: "kraft", text: `You are working on a Kraft work item.\nTitle: <title>\nTask: <task instruction>\nRepo: /Users/me/code/kraft\nWork item: <work item id>\nNode: ${q.get("task")?.split(".")[0] ?? "spec"}\nHook point: ${q.get("task") ?? ""}\nWorker session: <session id>\n` },
      { kind: "document", source: "spec", text: "\n\nWrite the spec to .engineering/specs/<work item id>.md." },
      { kind: "skill", source: "kraft:spec", text: "\n\n## Method\n\nUse the kraft:spec skill." },
      { kind: "steering", source: "repo:never-signal-processes-you-didnt-start", text: "\n\n## Project standards\n\nNever signal a process you did not start." },
      { kind: "steering", source: "task:project-standards", text: "\n\nKeep changes focused. Run the relevant checks before finishing.\n" },
    ] });
    if ((m = p.match(/^\/templates\/chains\/([^/]+)\/resolved$/))) {
      const tpl = st.templates.find((x) => x.id === decodeURIComponent(m![1]));
      if (!tpl) return json(route, { detail: `unknown chain template ${JSON.stringify(m[1])}` }, 404);
      return json(route, { id: tpl.id, chain: { nodes: tpl.nodes }, task_paths: [], steering: {}, nodes: tpl.nodes });
    }
    if ((m = p.match(/^\/templates\/chains\/([^/]+)$/))) {
      // For the /ng Chains editor, `default` is the real shipped file, the draft views' published side.
      // The shipped Settings pages keep the scenario's chain (settings-chains/default reads this route).
      if (m[1] === "default" && (req.headers()["referer"] ?? "").includes("/ng/")) return json(route, DRAFTS.published);
      const tpl = st.templates.find((x) => x.id === decodeURIComponent(m![1])) ?? st.templates[0];
      if (!tpl) return json(route, { detail: "template not found" }, 404);
      // The real route returns the file's text, which is what the Chains editor shows.
      return json(route, { id: tpl.id, file: `templates/chains/${tpl.id}.yaml`, text: chainYaml(tpl.nodes), chain: { nodes: tpl.nodes }, nodes: tpl.nodes });
    }
    // The Library screen (where Settings > Steering redirects): the scenario's hooks as its tasks.
    // The /ng editors' pickers read the real shipped library; the shipped Library page keeps the scenario's.
    if (p === "/templates/library" && method === "GET" && (req.headers()["referer"] ?? "").includes("/ng/")) return json(route, DRAFTS.library);
    if (p === "/templates/library" && method === "GET") {
      const hooks = Object.entries(st.hooks);
      const components = hooks.map(([name, definition]) => ({
        id: `tasks.${name}`, kind: "tasks", name, definition, issues: [],
        used_by: st.templates.filter((t) => t.nodes.some((n: any) => n.tasks?.includes(name))).map((t) => t.id),
        used_by_paths: st.templates.flatMap((t) => t.nodes.filter((n: any) => n.tasks?.includes(name)).map((n: any) => ({ chain: t.id, path: `${n.id}.main.${name}`, overrides: false }))),
      }));
      return json(route, { file: "templates/library.yaml", text: `tasks:\n${hooks.map(([k, v]) => `  ${k}: ${JSON.stringify(v)}`).join("\n")}\n`, components });
    }
    if (p === "/registry") return json(route, { hooks: st.hooks, invalid_templates: {} });
    if ((m = p.match(/^\/registry\/([^/]+)\/runs$/))) return json(route, { runs: Object.values(S.bundles).slice(0, 8).map((b, i) => ({ work_item_id: b.item.id, node_id: b.item.current_node_id ?? "verify", round: i % 3, status: ["done", "failed", "done", "capped_out"][i % 4], wall_ms: 120_000 + i * 40_000, created_at: b.item.updated_at })) });
    if (p === "/policy") return json(route, st.policy);
    // PUT merges over the file, as the real route does since B30.
    if (p === "/theme") return json(route, method === "PUT" ? Object.assign(st.theme, req.postDataJSON(), { derived: false }) : st.theme);
    if (p === "/steering") return json(route, st.steering);
    if ((m = p.match(/^\/steering\/([^/]+)$/))) return method === "DELETE" ? json(route, { deleted: m[1] }) : json(route, st.steeringBody(decodeURIComponent(m[1])));
    if (p === "/intake/checks") return json(route, [
      { id: 3, at: new Date(Date.now() - 60_000).toISOString(), ready: 2, started: ["w-2"], skipped: [{ bead_id: "B-9", reason: "max_concurrent" }] },
      { id: 2, at: new Date(Date.now() - 360_000).toISOString(), ready: 0, started: [], skipped: [] },
    ]);
    if (p === "/apply" || p === "/apply/reload") return json(route, {
      restart: [{ id: "access.port", file: "access.yaml", text: "port changes from 8765 to 9100" }],
      reload: [{ id: "disk:policy.yaml", file: "policy.yaml", text: "policy.yaml changed on disk since it was loaded", problem: "defaults: Input should be a valid dictionary" }],
      managed: true,
    });
    if (p === "/apply/restart") return route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ restarting: true }) });
    if (p === "/update" || p === "/update/check") return json(route, {
      installed: "1.4.0", latest: "v1.5.0", channel: "stable", behind: true, checked_at: new Date(Date.now() - 3_600_000).toISOString(),
    });
    if (p === "/intake") return json(route, st.intake);
    if (p === "/access") return json(route, st.access);
    if (p === "/notify") return json(route, st.notify);
    if (p === "/notify/test") return json(route, { at: new Date().toISOString(), status: 200, ms: 388, error: null });
    if (p === "/sessions") return json(route, st.sessions);
    if ((m = p.match(/^\/sessions\/[^/]+$/))) return route.fulfill({ status: 204 });
    if (p === "/analytics") return json(route, S.analytics);
    if (p === "/login") return json(route, { ok: true });
    if (p === "/logout") return route.fulfill({ status: 204 });

    return json(route, { detail: `sweep mock: unhandled ${method} ${p}` }, 404);
  });
}
