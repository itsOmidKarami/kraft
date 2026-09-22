import type { Page, Route } from "@playwright/test";
import { artifactFor, diffFor, documentDetail, searchFor, type Scenario } from "./fixtures";

export interface MockOptions {
  /** Every call except /health answers 401 → the Login screen. */
  locked?: boolean;
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

/**
 * Serve the whole /api surface from the scenario. Mutating verbs return a
 * plausible 200 so a composer's Submit never crashes a shot, but nothing
 * changes — the sweep is about how the UI looks, not what it does.
 */
export async function installMocks(page: Page, S: Scenario, opts: MockOptions = {}) {
  // The live-events socket: accept and stay silent so the shell reads "live".
  await page.routeWebSocket(/\/api\/ws\/events/, () => {});

  await page.route(/\/api\//, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname.replace(/^.*?\/api/, "");
    const method = req.method();
    const q = url.searchParams;

    if (p === "/health") return json(route, S.settings.health);
    if (opts.locked) {
      if (p === "/login" && method === "POST") return json(route, { ok: true });
      return json(route, { detail: "unauthenticated" }, 401);
    }

    let m: RegExpMatchArray | null;

    /* work items */
    if (p === "/work-items" && method === "GET") {
      const list = q.get("archived") === "true" ? S.archived : S.items;
      return json(route, { items: list, cursor: 4242 });
    }
    if (p === "/work-items" && method === "POST") return json(route, { id: S.items[0]?.id ?? "00000000000000000000000000000000" });
    if ((m = p.match(/^\/work-items\/([^/]+)$/))) {
      const b = S.bundles[m[1]];
      if (!b) return json(route, { detail: "work item not found" }, 404);
      if (method === "PATCH") return json(route, { id: m[1], ...(req.postDataJSON() ?? {}) });
      return json(route, { ...b.item, worker_sessions: b.sessions });
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/events$/))) {
      const b = S.bundles[m[1]];
      const after = Number(q.get("after_seq") ?? 0);
      return json(route, (b?.events ?? []).filter((e) => e.seq > after));
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/documents$/))) {
      return json(route, { work_item_id: m[1], documents: S.docs[m[1]] ?? [] });
    }
    if ((m = p.match(/^\/work-items\/([^/]+)\/diff$/))) return json(route, diffFor(m[1], S.variant));
    if ((m = p.match(/^\/work-items\/([^/]+)\/artifact$/))) {
      const b = S.bundles[m[1]];
      return b ? json(route, artifactFor(b.item, S.variant)) : json(route, { detail: "no artifact" }, 404);
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
    if ((m = p.match(/^\/work-items\/([^/]+)\/(pause|resume|retry|skip|abandon|archive|restore|escalate|open-worktree|budget\/raise|escalate\/stop|gates\/[^/]+\/(approve|reject))$/))) {
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
      if (method === "GET") return json(route, { repos: st.repos });
      if (method === "DELETE") return route.fulfill({ status: 204 });
      return json(route, { ...(st.repos[0] ?? {}), ...(req.postDataJSON() ?? {}) });
    }
    if (p === "/repos/probe") return json(route, { path: req.postDataJSON()?.path ?? "/tmp/x", name: "x", branch: "main", submodules: ["vendor/kraft-lite"], has_beads: true, beads_export_auto: false, beads_export_git_add: true, has_engineering: true, test_command: "uv run pytest -q", test_scopes: null, forge: "gitlab", project: "acme/x" });
    if (p === "/templates/chains") return json(route, st.templates);
    if (p === "/templates/parse") return json(route, { nodes: st.templates[0]?.nodes ?? [], error: null });
    if ((m = p.match(/^\/templates\/([^/]+)\/validate$/))) return json(route, { id: m[1], valid: true, error: null, unresolved: [] });
    if ((m = p.match(/^\/templates\/chains\/([^/]+)$/))) {
      const tpl = st.templates.find((x) => x.id === decodeURIComponent(m![1])) ?? st.templates[0];
      if (!tpl) return json(route, { detail: "template not found" }, 404);
      return json(route, { id: tpl.id, nodes: tpl.nodes });
    }
    if (p === "/registry") return json(route, { hooks: st.hooks, invalid_templates: {} });
    if ((m = p.match(/^\/registry\/([^/]+)\/runs$/))) return json(route, { runs: Object.values(S.bundles).slice(0, 8).map((b, i) => ({ work_item_id: b.item.id, node_id: b.item.current_node_id ?? "verify", round: i % 3, status: ["done", "failed", "done", "capped_out"][i % 4], wall_ms: 120_000 + i * 40_000, created_at: b.item.updated_at })) });
    if (p === "/policy") return json(route, st.policy);
    if (p === "/theme") return json(route, method === "PUT" ? req.postDataJSON() : st.theme);
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
