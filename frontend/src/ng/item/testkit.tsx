import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import type { ChainNode, ScopeRun, WorkItem, WorkerSession } from "../../types";
import { Shell } from "../shell/Shell";
import type { ItemDetail } from "./useItem";

/** Test helpers for ng/item: a V1 chain, an item on it, a fetch that records
 *  every call and answers by route, and rendering inside the Shell (the
 *  header's actions portal into it). Not a test file itself. */
export const V1: ChainNode[] = [
  { id: "plan", kind: "exec", gate_after: null, tasks: ["plan.write.plan"], steps: [["plan.write.plan"]] },
  { id: "plan_approval", kind: "gate", gate_after: null, tasks: [], steps: [], reject_to: "plan" },
  { id: "verification", kind: "exec", gate_after: null, tasks: ["verification.checks.lint", "verification.review.code_review"], steps: [["verification.checks.lint"], ["verification.review.code_review"]] },
  { id: "merge_request", kind: "exec", gate_after: null, tasks: ["merge_request.open.open_draft"], steps: [["merge_request.open.open_draft"]] },
];

/** `V1` as intake freezes it (`materialized_chain`), for an item that has not
 *  started: the chain's own values its Config rows read. */
export const FROZEN = JSON.stringify({
  chain: {
    nodes: [
      { id: "plan", kind: "exec", steps: [{ id: "write", tasks: [{ id: "plan", kind: "agent", harness: "claude", profile: "strong", prompt: "Write the plan." }] }] },
      { id: "plan_approval", kind: "gate" },
      { id: "verification", kind: "exec", fix_loop: { max_attempts: 2 }, steps: [
        { id: "checks", tasks: [{ id: "lint", kind: "subprocess", command: ["make", "lint"], policy: { time_cap_minutes: 10 } }] },
        { id: "review", tasks: [{ id: "code_review", kind: "agent", harness: "claude", model: "opus", prompt: "Review the change." }] },
      ] },
      { id: "merge_request", kind: "exec", policy: { budget_usd: 2 }, steps: [{ id: "open", tasks: [{ id: "open_draft", kind: "builtin" }] }] },
    ],
  },
  policy: { cap_defaults: { tasks: { time_cap_minutes: 60 } }, maxima: { nodes: { time_cap_minutes: 30 } } },
});
/** `FROZEN` with a repair and a judge in verification's fix loop: three rounds, two attempts and the first pass. */
export const LOOPED = JSON.stringify((() => {
  const m = JSON.parse(FROZEN);
  m.chain.nodes[2].fix_loop = { max_attempts: 2, tasks: [{ id: "repair", kind: "agent" }], judge: { id: "judge", kind: "agent" } };
  return m;
})());
/** The changed-test-scope task of the `verification` node's `tests` step, and the frozen chain that holds it. */
export const SCOPE_PATH = "verification.tests.test_changed_scopes";
export const scopeChain = (execution?: string, target?: unknown) =>
  JSON.stringify({
    chain: { nodes: [{ id: "verification", kind: "exec", fix_loop: { max_attempts: 2 }, steps: [{ id: "checks", tasks: [{ id: "lint", kind: "subprocess" }] }, { id: "tests", tasks: [{ id: "test_changed_scopes", kind: "builtin", ref: "kraft.verify_changed_test_scopes", ...(execution && { execution }) }] }] }] },
    ...(target ? { target } : {}),
  });
/** A workspace's root and two members: the order a fanned-out task visits them. */
export const WORKSPACE = { kind: "workspace", root: "ws", mounts: { pkg: { repository: "pkg", path: "repos/pkg" }, web: { repository: "web", path: "repos/web" } } };
let runs = 0;
/** One command a scope task ran: its `scope_runs` entry and its session, as the API lists them. */
export const scopeRun = (repository: string | null, command: string, round: number, status: string, over: Partial<ScopeRun> = {}, wall_ms: number | null = 24_000): [ScopeRun, WorkerSession] => {
  const id = `r${runs++}`;
  return [
    { session_id: id, node_id: "verification", hook_point: SCOPE_PATH, repository, round, command, passed: status === "done" ? true : status === "running" ? null : false, scope: command.replace("just test-", "") + "/**", ...over },
    { id, node_id: "verification", hook_point: SCOPE_PATH, status, attempt: 1, round, repository, command, wall_ms, started_at: "2026-09-13T10:09:00Z", created_at: "2026-09-13T10:09:00Z", model: null, thread: 1 } as unknown as WorkerSession,
  ];
};
/** A command a round picked and has not started: its `scope_runs` entry, and no session. */
export const pendingRun = (repository: string | null, command: string, round: number, over: Partial<ScopeRun> = {}): [ScopeRun, undefined] => [
  { session_id: null, node_id: "verification", hook_point: SCOPE_PATH, repository, round, command, passed: null, pending: true, selected: true, scope: command.replace("just test-", "") + "/**", ...over },
  undefined,
];
/** An item whose verification ran these scopes (`scopeRun`, `pendingRun`), on `scopeChain`. */
export const scoped = (runs: [ScopeRun, WorkerSession | undefined][], over: Partial<ItemDetail> = {}) =>
  detail({ materialized_chain: scopeChain(), repo: "/code/kraft-web", scope_runs: runs.map((r) => r[0]), worker_sessions: runs.flatMap((r) => (r[1] ? [r[1]] : [])), display_status: "needs_you", ...over });
/** An item filed with `FROZEN` and not started. */
export const fresh = (over: Partial<ItemDetail> = {}) => detail({ current_node_id: null, display_status: "paused", materialized_chain: FROZEN, ...over });

export const detail = (over: Partial<ItemDetail> = {}): ItemDetail =>
  ({
    id: "w1", title: "Design the cache", description: "Cache embeddings by content hash.", repo: "/code/kraft-plugins", status: "active",
    chain_template: "default", chain_definition: { template_id: "default", nodes: V1 }, current_node_id: "verification",
    bead_id: "kraft-cb59", created_at: "2026-09-13T08:00:00Z", updated_at: "2026-09-13T09:00:00Z",
    display_status: "running", stop: null, worker_sessions: [] as WorkerSession[],
    ...over,
  }) as ItemDetail;

/** One request `stubFetch` saw. `path` is the route without its query, which
 *  is what answers are keyed on; `url` keeps the query and `query` parses it,
 *  for a request that means something only through its parameters. Those two
 *  are not enumerable, so `toEqual({ method, path, body })` still reads as the
 *  request and a test about its query asks `query` for it. */
export type Call = { method: string; path: string; body: unknown; readonly url: string; readonly query: URLSearchParams };

function call(method: string, url: string, body: unknown): Call {
  const [path, search = ""] = url.split("?");
  return Object.defineProperties({ method, path, body } as Call, {
    url: { value: url, enumerable: false },
    query: { value: new URLSearchParams(search), enumerable: false },
  });
}

/** What an unstubbed read answers: the route's own empty shape, so a component
 *  a test didn't mean to feed never sees an object where the API sends a list. */
function emptyAnswer(path: string): [number, unknown] {
  if (/\/events$/.test(path) || /\/threads$/.test(path)) return [200, []];
  if (/\/diff$/.test(path)) return [200, { work_item_id: "w1", base_ref: null, files: [], diff: "", untracked: [], truncated: false }];
  if (/\/documents$/.test(path)) return [200, { work_item_id: "w1", documents: [] }];
  if (/\/log$/.test(path)) return [200, { session_id: "", status: "done", lines: [] }];
  if (path === "/editors") return [200, { available: [], system: false, default: null }];
  if (path === "/apply") return [200, { restart: [], reload: [], managed: false }];
  return [200, {}];
}

/** Stub fetch: `answers` maps "METHOD /path" (no /api, no query) to [status, body].
 *  An unrouted read answers its route's empty shape; an unrouted write is
 *  recorded and then refused, so no test passes on a write it never routed. */
export function stubFetch(answers: Record<string, [number, unknown]> = {}) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const c = call(method, String(input).replace(/^\/api/, ""), init?.body ? JSON.parse(String(init.body)) : undefined);
    calls.push(c);
    const routed = answers[`${method} ${c.path}`];
    if (!routed && method !== "GET") throw new TypeError(`stubFetch: no route for ${method} ${c.url}`);
    const [status, body] = routed ?? emptyAnswer(c.path);
    return new Response(JSON.stringify(body), { status });
  }));
  return calls;
}

/** `200 {}` for each "METHOD /path" named: the writes a test's page is
 *  expected to send, so that `stubFetch` answers them instead of refusing. */
export const acceptWrites = (...routes: string[]): Record<string, [number, unknown]> => Object.fromEntries(routes.map((r) => [r, [200, {}]]));

/** `stubFetch`, but every GET whose URL (query included, no /api) matches
 *  `held` waits until the test answers it, in any order: `calls[k](body)`
 *  answers the k-th, so a test can let an older read land after a newer one.
 *  `answers` is read at call time. */
export function holdFetch(held: RegExp, answers: Record<string, [number, unknown]> = {}) {
  const calls: ((body: unknown) => void)[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    const full = String(url).replace(/^\/api/, "");
    const path = full.split("?")[0];
    const method = init?.method ?? "GET";
    if (method === "GET" && held.test(full)) return new Promise<Response>((done) => calls.push((body) => done(new Response(JSON.stringify(body), { status: 200 }))));
    const routed = answers[`${method} ${path}`];
    if (!routed && method !== "GET") return Promise.reject(new TypeError(`holdFetch: no route for ${method} ${full}`));
    const [status, body] = routed ?? emptyAnswer(path);
    return Promise.resolve(new Response(JSON.stringify(body), { status }));
  }));
  return calls;
}

export const inShell = (ui: ReactElement, path = "/work-items/w1") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Shell />}>
          <Route path="*" element={ui} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

export type { WorkItem };
