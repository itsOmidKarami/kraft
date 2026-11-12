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

describe("Analytics is the board's", () => {
  it("names only nodes and gates of the shipped default chain", async () => {
    const a = await get("/analytics?range=8w");
    const names = [...a.by_node.map((n: any) => n.node), ...a.gate_waits.map((g: any) => g.gate), ...a.rejected_gates_by_gate.map((g: any) => g.gate)];
    expect(names.length).toBeGreaterThan(0);
    expect(names.filter((n) => !DEFAULT_NODES.has(n))).toEqual([]);
  });

  it("totals are sums over the board's items", async () => {
    const t = (await get("/analytics?range=8w")).totals;
    expect(t.work_items).toBe(board.length);
    expect(t.completed).toBe(board.filter((b) => b.item.status === "completed").length);
    expect(t.cost_usd).toBeCloseTo(sessions.reduce((a, s) => a + (s.cost_usd ?? 0), 0), 6);
    expect(t.tokens_out).toBe(sessions.reduce((a, s) => a + (s.tokens_out ?? 0), 0));
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
