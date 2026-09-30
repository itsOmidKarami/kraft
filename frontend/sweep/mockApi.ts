import type { Page, Route } from "@playwright/test";
import { NG_CHAINS, NG_REPOS, NG_WORKSPACES, ngDryRun } from "./ngBoard";
import { artifactFor, compareFor, diffFor, fixTargetFor, documentDetail, searchFor, type Scenario } from "./fixtures";
import { ngThreads } from "./ngItems";

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
}

/** A chain file as its author would write it: one mapping per node, nulls left out. */
const chainYaml = (nodes: Record<string, unknown>[]) =>
  "nodes:\n" + nodes.map((n) => Object.entries(n).filter(([, v]) => v != null)
    .map(([k, v], i) => `${i ? "    " : "  - "}${k}: ${Array.isArray(v) ? `[${v.join(", ")}]` : v}`).join("\n")).join("\n") + "\n";

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

/**
 * Serve the whole /api surface from the scenario. Mutating verbs return a
 * plausible 200 so a composer's Submit never crashes a shot, but nothing
 * changes — the sweep is about how the UI looks, not what it does.
 */
export async function installMocks(page: Page, S: Scenario, opts: MockOptions = {}) {
  // The live-events socket: accept and stay silent so the shell reads "live".
  await page.routeWebSocket(/\/api\/ws\/events/, (ws) => { if (opts.boardState === "offline") ws.close(); });
  let listReads = 0;

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
    /* config drafts: one static answer per key; `stale` publishes to a 409 */
    if ((m = p.match(/^\/drafts\/(chains|library)\/([^/]+)(?:\/(undo|publish)|\/files\/(.+))?$/))) {
      const [, area, key, action, file] = m;
      const name = area === "library" ? "library.yaml" : `chains/${key}.yaml`;
      const text = file ? req.postDataJSON()?.text : area === "library" ? "tasks: {}\n" : chainYaml(st.templates[0]?.nodes ?? []);
      const change = { path: "implement.main.implement", kind: "change", summary: "model", fields: ["model"] };
      const result = {
        model: { [name]: { nodes: st.templates[0]?.nodes ?? [] } },
        resolved: null, sources: {}, warnings: [],
        impact: area === "chains" ? { running: 2, repos: ["/Users/me/code/kraft"] } : null,
        problems: key === "broken" ? [{ path: "implement.main.implement", field: "bogus", message: "Extra inputs are not permitted", file: name, line: 7, col: 9 }] : [],
        changes: key === "library" ? [] : [change],
        ...(key === "yaml-error" ? { yaml_error: { file: name, line: 4, col: 3, message: "expected ',' or ']', but got '<stream end>'" } } : {}),
      };
      if (method === "DELETE") return route.fulfill({ status: 204 });
      if (action === "publish") {
        if (key === "stale") return json(route, { detail: `published since this draft began: ${name}`, files: { [name]: { published: text, draft: text, diff: `--- published/${name}\n+++ draft/${name}\n@@ -3 +3 @@\n-    model: sonnet\n+    model: opus\n` } } }, 409);
        return json(route, { published: [name], result: { ...result, changes: [] } });
      }
      return json(route, { area, key, draft: true, files: { [name]: text }, base: { [name]: "9f2c".padEnd(64, "0") }, updated_at: "2026-10-01T09:12:00Z", result });
    }
    if (p === "/drafts") return json(route, ["default", "broken", "yaml-error", "stale"].map((key) => ({ area: "chains", key, files: [`chains/${key}.yaml`], changes: 1, problems: key === "broken" ? 1 : 0, updated_at: "2026-10-01T09:12:00Z" })));
    if (p === "/templates/chains") return json(route, opts.ngBoard ? NG_CHAINS : st.templates);
    if (p === "/templates/parse") return json(route, { nodes: st.templates[0]?.nodes ?? [], error: null });
    if ((m = p.match(/^\/templates\/([^/]+)\/validate$/))) return json(route, { id: m[1], valid: true, error: null, unresolved: [] });
    if ((m = p.match(/^\/templates\/chains\/([^/]+)\/resolved$/))) {
      const tpl = st.templates.find((x) => x.id === decodeURIComponent(m![1]));
      if (!tpl) return json(route, { detail: `unknown chain template ${JSON.stringify(m[1])}` }, 404);
      return json(route, { id: tpl.id, chain: { nodes: tpl.nodes }, task_paths: [], steering: {}, nodes: tpl.nodes });
    }
    if ((m = p.match(/^\/templates\/chains\/([^/]+)$/))) {
      const tpl = st.templates.find((x) => x.id === decodeURIComponent(m![1])) ?? st.templates[0];
      if (!tpl) return json(route, { detail: "template not found" }, 404);
      // The real route returns the file's text, which is what the Chains editor shows.
      return json(route, { id: tpl.id, file: `templates/chains/${tpl.id}.yaml`, text: chainYaml(tpl.nodes), chain: { nodes: tpl.nodes }, nodes: tpl.nodes });
    }
    // The Library screen (where Settings > Steering redirects): the scenario's hooks as its tasks.
    if (p === "/templates/library" && method === "GET") {
      const hooks = Object.entries(st.hooks);
      const components = hooks.map(([name, definition]) => ({
        id: `tasks.${name}`, kind: "tasks", name, definition, issues: [],
        used_by: st.templates.filter((t) => t.nodes.some((n: any) => n.tasks?.includes(name))).map((t) => t.id),
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
