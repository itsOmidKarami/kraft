import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildScenario, type Scenario } from "../e2e/contract/mocks/fixtures";
import { mockServer, type Answer, type MockOptions } from "../e2e/contract/mocks/mockApi";
import { FRESH_TITLES } from "../e2e/contract/mocks/motion";
import { NG_NOW } from "../e2e/contract/mocks/ngItems";
import type { Ev } from "../e2e/contract/mocks/transitions";

/** The demo's live motion (motion.ts), on fake timers: what one tick does to the board's running items. */

/** With `random` at 0 every tick is 8 s after the last, and every session reports the least usage. */
const TICK = 8_000;
const started = (motion: MockOptions["motion"] = { random: () => 0 }) => {
  const S = buildScenario("default");
  const told: Ev[] = [];
  const serve = mockServer(S, { ngBoard: true, now: () => NG_NOW, onEvent: (e) => told.push(e), motion });
  const call = async (method: string, path: string, body?: unknown) => {
    const a = (await serve({ method, url: `http://x/api${path}`, json: () => body ?? null })) as Answer;
    return { status: a.status, body: a.body ? JSON.parse(a.body) : null };
  };
  /** Run `n` ticks; what they appended to `id`, as [type, payload] pairs. */
  const ticks = (n: number, id?: string) => {
    const had = told.length;
    vi.advanceTimersByTime(TICK * n);
    return told.slice(had).filter((e) => !id || e.work_item_id === id).map((e) => [e.type, e.payload] as const);
  };
  return { S, serve, call, told, ticks };
};
/** A board row's id by its bead. */
const board = (S: Scenario, bead: string) => S.ngBoard.find((id) => S.bundles[id].item.bead_id === bead)!;
const RUNNING = "kraft-91bc";
const GATED = "kraft-cb59";
const FAILED = "kraft-b108";
/** The other board item that is active at the start (the escalated row: its turn is done, so it runs). */
const ALSO_ACTIVE = "kraft-2c77";
const types = (evs: readonly (readonly [string, unknown])[]) => evs.map(([t]) => t);

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe("one step", () => {
  it("an agent node's session ends with its usage, and the next node's session starts", async () => {
    const { S, ticks, call, told } = started();
    const id = board(S, RUNNING);
    const b = S.bundles[id];
    const was = b.sessions.find((s) => s.status === "running")!;
    const top = Math.max(...Object.values(S.bundles).flatMap((x) => x.events.map((e) => e.seq)));
    expect(ticks(1, id)).toEqual([
      ["worker_session_exited", { session_id: was.id, status: "done", wall_ms: expect.any(Number), model: "claude-sonnet-4-5", tokens_in: 28_000, tokens_cache_write: 0, tokens_cache_read: 0, tokens_out: 3_000, cost_usd: 0.04 }],
      ["node_completed", { node_id: "verification" }],
      ["node_started", { node_id: "work_brief" }],
      ["worker_session_created", { session_id: expect.any(String), node_id: "work_brief", hook_point: "work_brief.write.work_brief", round: 0, attempt: 1, thread: 1 }],
      ["worker_session_started", expect.objectContaining({ session_id: expect.any(String), node_id: "work_brief", hook_point: "work_brief.write.work_brief", attempt: 1 })],
    ]);
    const now = b.sessions.at(-1)!;
    expect([was.status, was.cost_usd, was.tokens_in]).toEqual(["done", 0.04, 28_000]);
    expect([now.node_id, now.status, now.started_at, b.logs[now.id].length > 0]).toEqual(["work_brief", "running", NG_NOW, true]);
    // seq goes on from the sample data's highest, and every event sent is on its item's bundle.
    expect(told[0].seq).toBeGreaterThan(top);
    expect(told.map((e) => e.seq)).toEqual(told.map((_, k) => told[0].seq + k));
    expect(told.filter((e) => e.work_item_id === id)).toEqual(b.events.slice(-5));
    // The list's row and the detail read the same item.
    const row = (await call("GET", "/work-items")).body.items.find((i: any) => i.id === id);
    const detail = (await call("GET", `/work-items/${id}`)).body;
    expect([row.current_node_id, row.display_status, row.step]).toEqual(["work_brief", "running", null]);
    expect(detail.worker_sessions.at(-1)).toMatchObject({ id: now.id, status: "running" });
  });

  it("names the step of a node of several steps, as the list row draws it", async () => {
    const { S, ticks, call } = started();
    const id = board(S, GATED);
    await call("POST", `/work-items/${id}/gates/final_review/reject`, { note: "again" });
    ticks(2, id);
    // verification's first agent task is the security scan, in the second of its three steps.
    expect(S.bundles[id].item).toMatchObject({ current_node_id: "verification", step: { index: 2, count: 3, name: "checks", task: "security_scan" }, summary: expect.objectContaining({ step: { index: 2, count: 3 } }) });
  });

  it("a node with no agent task starts and completes in one tick, with no session", async () => {
    const { S, ticks, call } = started();
    const id = board(S, FAILED);
    const had = S.bundles[id].sessions.length;
    expect((await call("POST", `/work-items/${id}/retry`, {})).status).toBe(200);
    expect(ticks(1, id)).toEqual([["node_started", { node_id: "merge_request" }], ["node_completed", { node_id: "merge_request" }]]);
    expect(ticks(1, id)).toEqual([["node_started", { node_id: "mr_checks" }], ["node_completed", { node_id: "mr_checks" }]]);
    expect(S.bundles[id].sessions.length).toBe(had);
  });

  it("stops at a gate, asking for it with its artifact, and goes no further", () => {
    const { S, ticks } = started();
    const id = board(S, RUNNING);
    ticks(1, id);
    expect(ticks(1, id).slice(2)).toEqual([["node_started", { node_id: "local_review" }], ["gate_requested", { gate: "local_review", node_id: "local_review" }]]);
    expect(S.bundles[id].item).toMatchObject({
      status: "needs_human", display_status: "needs_you", current_node_id: "local_review", pending_gate: "local_review",
      gate_artifact: `items/${id}/docs/work_brief.md`, stop: { kind: "gate", node: "local_review" },
    });
    expect(ticks(3, id)).toEqual([]);
  });
});

describe("after the visitor", () => {
  it("approve sets it going: the next tick steps past the gate", async () => {
    const { S, ticks, call } = started();
    const id = board(S, GATED);
    expect(ticks(2, id)).toEqual([]);
    expect((await call("POST", `/work-items/${id}/gates/final_review/approve`)).status).toBe(200);
    expect(ticks(1, id)).toEqual([["node_started", { node_id: "release" }], ["node_completed", { node_id: "release" }]]);
  });

  it("past the last node it completes, shown done", async () => {
    const { S, ticks, call } = started();
    const id = board(S, GATED);
    await call("POST", `/work-items/${id}/gates/final_review/approve`);
    ticks(1, id);
    expect(ticks(1, id)).toEqual([["work_item_completed", {}]]);
    expect(S.bundles[id].item).toMatchObject({ status: "completed", display_status: "done", pending_gate: null });
    expect(ticks(3, id)).toEqual([]);
  });

  it("reject sends it back to the gate's reject_to, which runs again", async () => {
    const { S, ticks, call } = started();
    const id = board(S, GATED);
    expect((await call("POST", `/work-items/${id}/gates/final_review/reject`, { note: "cache is unbounded" })).status).toBe(200);
    expect(types(ticks(1, id))).toEqual(["node_started", "worker_session_created", "worker_session_started"]);
    expect(S.bundles[id].sessions.at(-1)).toMatchObject({ node_id: "implementation", hook_point: "implementation.build.implement", status: "running" });
  });

  it("a resume with a steer starts the attempt it queued", async () => {
    const { S, ticks, call } = started();
    const id = board(S, RUNNING);
    await call("POST", `/work-items/${id}/pause`);
    await call("POST", `/work-items/${id}/resume`, { steer: "look at the invalidation path" });
    const queued = S.bundles[id].sessions.at(-1)!;
    expect(ticks(1, id)).toEqual([["worker_session_started", expect.objectContaining({ session_id: queued.id, node_id: "verification" })]]);
    expect(types(ticks(1, id))).toEqual(["worker_session_exited", "node_completed", "node_started", "worker_session_created", "worker_session_started"]);
  });
});

describe("when the board goes quiet", () => {
  /** Pause both running items; `gate` also ends the one waiting at a gate. */
  const quiet = async (call: ReturnType<typeof started>["call"], S: Scenario, gate: boolean) => {
    for (const bead of [RUNNING, ALSO_ACTIVE]) expect((await call("POST", `/work-items/${board(S, bead)}/pause`)).status).toBe(200);
    if (gate) expect((await call("POST", `/work-items/${board(S, GATED)}/cancel`, { reason: "superseded" })).status).toBe(200);
  };

  it.each([
    // No gate waits once the gated item is cancelled: only the running items hold it back.
    ["while an item is active", async (call: ReturnType<typeof started>["call"], S: Scenario) => {
      expect((await call("POST", `/work-items/${board(S, GATED)}/cancel`, { reason: "superseded" })).status).toBe(200);
    }],
    ["while a gate waits", async (call: ReturnType<typeof started>["call"], S: Scenario) => quiet(call, S, false)],
  ])("files nothing %s", async (_, setup) => {
    const { S, ticks, call } = started();
    const had = S.ngBoard.length;
    await setup(call, S);
    ticks(4);
    expect(S.ngBoard.length).toBe(had);
  });

  it("files a fresh item once nothing is active and no gate waits, and starts it", async () => {
    const { S, ticks, call } = started();
    const had = S.ngBoard.length;
    await quiet(call, S, true);
    const evs = ticks(1);
    const id = S.ngBoard.at(-1)!;
    expect(S.ngBoard.length).toBe(had + 1);
    expect(S.bundles[id].item).toMatchObject({ title: FRESH_TITLES[0], status: "active", display_status: "running", current_node_id: "spec" });
    expect(types(evs)).toEqual(["work_item_created", "node_started", "worker_session_created", "worker_session_started"]);
    // It reaches its first gate, and while that waits nothing more is filed.
    ticks(1);
    expect(S.bundles[id].item.pending_gate).toBe("spec_approval");
    ticks(5);
    expect(S.ngBoard.length).toBe(had + 1);
  });
});

describe("what never moves", () => {
  it("nothing, while paused; it goes on once resumed", () => {
    const { serve, ticks } = started();
    serve.motion!.pause();
    expect(ticks(10)).toEqual([]);
    serve.motion!.resume();
    expect(ticks(1).length).toBeGreaterThan(0);
  });

  it("nothing, in a mock without motion, however long it runs", () => {
    const { S, serve, ticks } = started(false);
    const before = JSON.stringify(S.bundles);
    expect([serve.motion, ticks(20)]).toEqual([null, []]);
    expect(JSON.stringify(S.bundles)).toBe(before);
  });

  it("the item page's scenario bundles, which are not on the board", () => {
    const { S, ticks } = started();
    const off = Object.keys(S.bundles).filter((id) => !S.ngBoard.includes(id));
    expect(off).toContain(S.ng.running);
    expect(S.bundles[S.ng.running].item.status).toBe("active");
    const before = JSON.stringify(off.map((id) => S.bundles[id]));
    ticks(12);
    expect(JSON.stringify(off.map((id) => S.bundles[id]))).toBe(before);
  });

  it.each([
    ["the shortest pause", () => 0, 8_000],
    ["the longest", () => 1, 15_000],
  ])("each tick waits its jittered 8–15 s: %s", (_, random, wait) => {
    const { told } = started({ random });
    vi.advanceTimersByTime(wait - 1);
    expect(told).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(told.length).toBeGreaterThan(0);
  });
});
