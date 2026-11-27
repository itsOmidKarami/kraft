// @vitest-environment node
import { describe, expect, it } from "vitest";
import { buildScenario } from "../e2e/contract/mocks/fixtures";
import { mockServer, type Answer } from "../e2e/contract/mocks/mockApi";
import { NG_NOW } from "../e2e/contract/mocks/clock";
import shipped from "../e2e/contract/mocks/shippedChains.json" with { type: "json" };
import { demoOptions } from "./scenario";

/**
 * The demo's Analytics, Harnesses and Workers pages hold to its board: each rule reads the board's items or the shipped
 * chains on its own, never the mock's arithmetic, and asks the mock what the demo serves.
 */
const S = buildScenario("default");
const serve = mockServer(S, demoOptions);
const get = async (path: string) => JSON.parse(((await serve({ method: "GET", url: `http://demo/api${path}`, json: () => null })) as Answer).body!);
const board = [...S.ngBoard, ...S.ngArchived].map((id) => S.bundles[id]);
const sessions = board.flatMap((b) => b.sessions);
const DEFAULT_NODES = new Set(shipped.chains.default.chain.nodes.map((n: any) => n.id));
/** Every task path of a shipped chain: its steps' (`<node>.<step>.<task>`), those it lists directly (step `main`), its fix loop's and the loop's judge. */
const SHIPPED_TASKS = new Set(Object.entries(shipped.chains).flatMap(([chain, c]) => (c.chain.nodes as any[]).flatMap((n) => {
  const home = (prefix: string, h: any): string[] => [
    ...(h?.steps ?? []).flatMap((st: any) => (st.tasks ?? []).map((t: any) => `${prefix}.${st.id}.${t.id}`)),
    ...(h?.tasks ?? []).map((t: any) => `${prefix}.main.${t.id}`),
  ];
  return [...home(n.id, n), ...home(`${n.id}.fix_loop`, n.fix_loop), ...(n.fix_loop?.judge ? [`${n.id}.fix_loop.judge`] : [])].map((p) => `${chain}:${p}`);
})));
const MAX_ATTEMPTS = Math.min(...Object.values(shipped.chains).flatMap((c) => (c.chain.nodes as any[]).filter((n) => n.fix_loop).map((n) => n.fix_loop.max_attempts)));

const KINDS = ["tokens_in", "tokens_cache_write", "tokens_cache_read", "tokens_out"];
const tokensOf = (s: any) => KINDS.reduce((a, k) => a + (s[k] ?? 0), 0);
/** A session's wall time as `analytics.compute` reads it: its own, or its stamps' span, a live one's to the clock. */
const wallOf = (s: any) => s.wall_ms ?? Math.max(0, (Date.parse(s.exited_at) || Date.parse(NG_NOW)) - Date.parse(s.started_at ?? s.created_at));
/** By `key`, the sessions' cost (to a millionth), every token and wall time, as rows of `[key, cost, tokens, wall]`. */
const sums = (key: (b: any, s: any) => string, bundles = board) => {
  const out: Record<string, [number, number, number]> = {};
  for (const b of bundles) for (const s of b.sessions) {
    const r = (out[key(b, s)] ??= [0, 0, 0]);
    r[0] += s.cost_usd ?? 0; r[1] += tokensOf(s); r[2] += wallOf(s);
  }
  return Object.entries(out).map(([k, [c, t, w]]) => [k, Math.round(c * 1e6), t, w]).sort();
};
/** The stop buckets `_episode_bucket` writes besides a gate's (`gate · <gate>`). */
const STOPS = new Set(["budget", "cap breach", "orchestrator/infra defect", "preventable dispatch", "task failure"]);

describe("Analytics is the board's", () => {
  it("names only nodes, gates, stops and repos of the shipped default chain and the board", async () => {
    const a = await get("/analytics?range=8w");
    const names = [...a.by_node.map((n: any) => n.node), ...a.gate_waits.map((g: any) => g.gate), ...a.rejected_gates_by_gate.map((g: any) => g.gate)];
    expect([names.length > 0, a.stop_reasons.length > 0]).toEqual([true, true]);
    expect(names.filter((n) => !DEFAULT_NODES.has(n))).toEqual([]);
    expect(a.stop_reasons.map((r: any) => r.label).filter((l: string) => !STOPS.has(l) && !DEFAULT_NODES.has(l.match(/^gate · (.+)$/)?.[1]))).toEqual([]);
    expect(a.by_repo.map((r: any) => r.repo).sort()).toEqual([...new Set(board.map((b) => b.item.repo))].sort());
  });

  it("each node's and each repo's cost, tokens and time are sums over the board's sessions", async () => {
    const a = await get("/analytics?range=8w");
    expect(a.by_node.map((n: any) => [n.node, Math.round(n.cost_usd * 1e6), n.tokens, n.wall_ms]).sort()).toEqual(sums((_b, s) => s.node_id));
    expect(a.by_repo.map((r: any) => [r.repo, Math.round(r.cost_usd * 1e6), r.tokens]).sort()).toEqual(sums((b) => b.item.repo).map((r) => r.slice(0, 3)));
    expect(a.by_repo.map((r: any) => [r.repo, r.items]).sort()).toEqual([...new Set(board.map((b) => b.item.repo))].map((r) => [r, board.filter((b) => b.item.repo === r).length]).sort());
  });

  it("a gate's waits are no more than it was asked, and the median lead time is one of the finished items'", async () => {
    const a = await get("/analytics?range=8w");
    const asked = (gate: string) => board.flatMap((b) => b.events).filter((e) => e.type === "gate_requested" && e.payload.gate === gate).length;
    expect(a.gate_waits.filter((g: any) => !(g.n > 0 && g.n <= asked(g.gate))).map((g: any) => g.gate)).toEqual([]);
    const leads = board.filter((b) => b.item.status === "completed").map((b) => Date.parse(b.events.find((e) => e.type === "work_item_completed")!.created_at) - Date.parse(b.events.find((e) => e.type === "node_started")!.created_at));
    expect(Math.min(...leads) <= a.totals.median_lead_ms && a.totals.median_lead_ms <= Math.max(...leads)).toBe(true);
  });

  it("a repo filter and a shorter range count only the items they pick", async () => {
    const repo = board[0].item.repo;
    const mine = board.filter((b) => b.item.repo === repo);
    const one = (await get(`/analytics?range=8w&repo=${encodeURIComponent(repo)}`)).totals;
    expect([one.work_items, Math.round(one.cost_usd * 1e6)]).toEqual([mine.length, Math.round(mine.flatMap((b) => b.sessions).reduce((x, s) => x + (s.cost_usd ?? 0), 0) * 1e6)]);
    const week = board.filter((b) => Date.parse(b.item.created_at) >= Date.parse(NG_NOW) - 7 * 86_400_000);
    expect([(await get("/analytics?range=7d")).totals.work_items, week.length > 0, week.length < board.length]).toEqual([week.length, true, true]);
  });

  it.each(["long", "many", "empty"] as const)("the %s variant still serves its own analytics", async (variant) => {
    const V = buildScenario(variant);
    const body = ((await mockServer(V, demoOptions)({ method: "GET", url: "http://demo/api/analytics?range=8w", json: () => null })) as Answer).body!;
    expect(JSON.parse(body)).toEqual(JSON.parse(JSON.stringify(V.analytics)));
  });

  it("totals are sums over the board's items", async () => {
    const t = (await get("/analytics?range=8w")).totals;
    expect(t.work_items).toBe(board.length);
    expect(t.completed).toBe(board.filter((b) => b.item.status === "completed").length);
    expect(t.cost_usd).toBeCloseTo(sessions.reduce((a, s) => a + (s.cost_usd ?? 0), 0), 6);
    expect(KINDS.map((k) => t[k])).toEqual(KINDS.map((k) => sessions.reduce((a, s) => a + (s[k] ?? 0), 0)));
    expect(t.wall_ms).toBe(sessions.reduce((a, s) => a + wallOf(s), 0));
    const statuses = [...new Set(board.map((b) => b.item.status))];
    expect(t.by_status).toEqual(Object.fromEntries(statuses.map((st) => [st, board.filter((b) => b.item.status === st).length])));
    expect(t.mrs_merged).toBe(board.flatMap((b) => b.events).filter((e) => e.type === "node_completed" && e.payload.node_id === "merge").length);
  });

  it("repairs per item are the items' own fix cycles, below the shipped cap", async () => {
    const t = (await get("/analytics?range=8w")).totals;
    const cycles = board.map((b) => b.events.filter((e) => e.type === "fix_cycle_finished").length).filter((n) => n > 0);
    expect(cycles.length).toBeGreaterThan(0);
    expect(t.fix_cycles).toBe(Math.round((cycles.reduce((a, n) => a + n, 0) / cycles.length) * 10) / 10);
    expect(t.fix_cycles).toBeLessThan(MAX_ATTEMPTS);
  });

  it("weekly buckets are Mondays, oldest first, none after the demo's clock", async () => {
    const weeks = (await get("/analytics?range=8w")).weekly_merged.map((w: any) => w.week_start);
    expect(weeks.length).toBeGreaterThan(0);
    expect(weeks.filter((w: string) => new Date(w).getUTCDay() !== 1 || w > NG_NOW.slice(0, 10))).toEqual([]);
    expect(weeks).toEqual([...weeks].sort());
  });
});

it("every harness lane names a task of the shipped chains", async () => {
  const lanes = (await get("/drafts/harnesses/harnesses")).result.resolved.harnesses;
  const paths = lanes.flatMap((h: any) => h.tasks.map((t: any) => `${t.chain}:${t.path}`));
  expect(paths.length).toBeGreaterThan(0);
  expect(paths.filter((p: string) => !SHIPPED_TASKS.has(p))).toEqual([]);
});

it("a machine runs the board's running sessions on it, and no more", async () => {
  const { workers } = await get("/workers");
  const runs = (name: string | null) => sessions.filter((s) => s.status === "running" && (s.runner ?? null) === name).length;
  expect(workers.map((w: any) => [w.name, w.slots_used])).toEqual(workers.map((w: any) => [w.name, runs(w.mode === "local" ? null : w.name)]));
});
