import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildScenario, type Scenario } from "../e2e/contract/mocks/fixtures";
import { mockServer, type Answer, type MockOptions } from "../e2e/contract/mocks/mockApi";
import { FRESH_TITLES } from "../e2e/contract/mocks/motion";
import { NG_NOW } from "../e2e/contract/mocks/ngItems";
import { RESUME_PROMPT, type Ev } from "../e2e/contract/mocks/transitions";
import { recent } from "../src/ng/item/events";
import { budgetRaise } from "../src/ng/item/status";

/** The demo's live motion (motion.ts), on fake timers: what one tick does to the board's running items. */

/** With `random` at 0 every tick is 8 s after the last, and every session reports the least usage. */
const TICK = 8_000;
/** `"omit"` leaves `motion` out of the options altogether, as the contract suite calls the mock. */
const started = (motion: MockOptions["motion"] | "omit" = { random: () => 0 }, now: () => string = () => NG_NOW) => {
  const S = buildScenario("default");
  const told: Ev[] = [];
  const serve = mockServer(S, { ngBoard: true, now, onEvent: (e) => told.push(e), ...(motion === "omit" ? {} : { motion }) });
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
/** The escalated row: stopped, an escalation turn running on it, so it is not active. */
const ESCALATED = "kraft-2c77";
/** Filed paused and never started. */
const NEVER_STARTED = "kraft-f5d3";
/** Rate limited on verification, its retry due at 10:13:42 (NG_NOW is 10:10). */
const WAITING = "kraft-e410";
const count = (S: Scenario, id: string, type: string, node: string) => S.bundles[id].events.filter((e) => e.type === type && e.payload.node_id === node).length;
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

  it("a node with no agent task takes two ticks: a session per task created, then exited with no usage; its draft task opens the MR", async () => {
    const { S, ticks, call } = started();
    const id = board(S, FAILED);
    expect((await call("POST", `/work-items/${id}/retry`, {})).status).toBe(200);
    expect(ticks(1, id)).toEqual([
      ["node_started", { node_id: "merge_request" }],
      ["worker_session_created", expect.objectContaining({ node_id: "merge_request", hook_point: "merge_request.rebase.rebase" })],
      ["worker_session_created", expect.objectContaining({ node_id: "merge_request", hook_point: "merge_request.open.open_draft", attempt: 4 })],
    ]);
    expect(S.bundles[id].item).toMatchObject({ current_node_id: "merge_request", display_status: "running" });
    // Stamped started as it is made, as the store writes every session; a builtin's is never `running`.
    expect(S.bundles[id].sessions.slice(-2).map((s) => [s.status, s.started_at])).toEqual([["pending", NG_NOW], ["pending", NG_NOW]]);
    expect(ticks(1, id)).toEqual([
      ["worker_session_exited", { session_id: expect.any(String), status: "done", wall_ms: expect.any(Number) }],
      ["mr_opened", { number: 150, url: "https://github.com/acme/kraft-api/pull/150" }],
      ["worker_session_exited", { session_id: expect.any(String), status: "done", wall_ms: expect.any(Number) }],
      ["node_completed", { node_id: "merge_request" }],
      ["node_started", { node_id: "mr_checks" }],
      ["worker_session_created", expect.objectContaining({ hook_point: "mr_checks.ci.mr_checks" })],
    ]);
    // No node is left standing finished: the tick that completed it entered the next.
    expect(S.bundles[id].item.current_node_id).toBe("mr_checks");
    expect(S.bundles[id].sessions.filter((s) => s.node_id === "merge_request").slice(-2).map((s) => [s.status, s.cost_usd])).toEqual([["done", null], ["done", null]]);
    expect(S.bundles[id].item.mr_ref).toEqual({ number: 150, url: "https://github.com/acme/kraft-api/pull/150" });
    expect(S.bundles[id].events.find((e) => e.type === "mr_opened")?.node_id).toBe("merge_request");
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
    expect(types(ticks(1, id))).toEqual(["node_started", "worker_session_created"]);
    expect(S.bundles[id].item.current_node_id).toBe("release");
  });

  it("approve counts its gate at once, and a re-approve after a reject writes no second node_completed", async () => {
    const { S, ticks, call } = started();
    const id = board(S, GATED);
    const passed = () => S.bundles[id].item.summary.gates_passed;
    const before = passed();
    await call("POST", `/work-items/${id}/gates/final_review/approve`);
    expect(passed()).toBe(before + 1);
    // Changes asked at implementation while it runs on: back there once release completes (no fork), then round to
    // the gate again, which asks again since a later gate's decision is the newest.
    expect((await call("POST", `/work-items/${id}/review`, { outcome: "request_changes", node: "implementation" })).body.action).toBe("queued");
    for (let k = 0; k < 16 && S.bundles[id].item.pending_gate !== "final_review"; k++) {
      ticks(1, id);
      const g = S.bundles[id].item.pending_gate;
      if (g && g !== "final_review") await call("POST", `/work-items/${id}/gates/${g}/approve`);
    }
    expect(S.bundles[id].item.pending_gate).toBe("final_review");
    await call("POST", `/work-items/${id}/gates/final_review/approve`);
    expect(count(S, id, "node_completed", "final_review")).toBe(1);
  });

  it("completes in the tick its last node completes, shown done", async () => {
    const { S, ticks, call } = started();
    const id = board(S, GATED);
    await call("POST", `/work-items/${id}/gates/final_review/approve`);
    ticks(1, id);
    expect(types(ticks(1, id))).toEqual(["worker_session_exited", "node_completed", "work_item_completed"]);
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
    // The walk enters its node again before the task starts (`dispatch`).
    expect(ticks(1, id)).toEqual([["node_started", { node_id: "verification" }], ["worker_session_started", expect.objectContaining({ session_id: queued.id, node_id: "verification" })]]);
    expect(types(ticks(1, id))).toEqual(["worker_session_exited", "node_completed", "node_started", "worker_session_created", "worker_session_started"]);
  });

  it("Start on an item never started reads 0 of N, and runs its first node", async () => {
    const { S, ticks, call } = started();
    const id = board(S, NEVER_STARTED);
    const b = S.bundles[id];
    expect([b.sessions, b.events.map((e) => e.type), b.item.budget_cap.spent_usd, b.item.usage.total.cost_usd, b.item.worktree_exists]).toEqual([[], ["work_item_created"], 0, 0, false]);
    expect((await call("GET", `/work-items/${id}/diff`)).body.files).toEqual([]);
    expect(ticks(2, id)).toEqual([]);
    expect((await call("POST", `/work-items/${id}/resume`, {})).status).toBe(200);
    expect((await call("GET", `/work-items/${id}`)).body.summary).toEqual({ nodes_done: 0, nodes_total: 15, gates_passed: 0, step: null });
    expect(ticks(1, id)).toEqual([
      ["node_started", { node_id: "spec" }],
      ["worker_session_created", expect.objectContaining({ node_id: "spec", hook_point: "spec.write.spec", attempt: 1 })],
      ["worker_session_started", expect.objectContaining({ node_id: "spec" })],
    ]);
    expect(b.sessions.map((s) => `${s.node_id}:${s.status}`)).toEqual(["spec:running"]);
    expect(b.item.worktree_exists).toBe(true);
  });

  it.each([
    // The walk runs one task a node: skipping it ends the node, and the task is not run again.
    ["a skip of the task it runs ends the node", { path: "work_brief.write.work_brief" }, [["node_completed", { node_id: "work_brief" }]]],
    ["a node skip passes the node", {}, []],
  ])("%s: the walk moves on", async (_, body, first) => {
    const { S, ticks, call } = started();
    const id = board(S, RUNNING);
    ticks(1, id);
    expect((await call("POST", `/work-items/${id}/skip`, body)).status).toBe(200);
    expect(ticks(1, id)).toEqual([...first, ["node_started", { node_id: "local_review" }], ["gate_requested", { gate: "local_review", node_id: "local_review" }]]);
    expect(S.bundles[id].sessions.filter((s) => s.hook_point === "work_brief.write.work_brief").map((s) => s.status)).toEqual(["paused"]);
  });

  it("a skip refuses a scope outside the node the item stands on, in the server's words", async () => {
    const { S, call } = started();
    const id = board(S, RUNNING);
    expect(await call("POST", `/work-items/${id}/skip`, { path: "implementation.build" })).toEqual({ status: 409, body: { detail: "path: 'implementation.build' is not inside the node the item stands on ('verification')" } });
  });

  it("a skipped step stops only its own sessions, and is passed over when a reject reruns its node", async () => {
    const { S, ticks, call } = started();
    const id = board(S, RUNNING);
    const running = S.bundles[id].sessions.find((s) => s.status === "running")!;
    expect((await call("POST", `/work-items/${id}/skip`, { path: "verification.checks" })).status).toBe(200);
    expect(running.status).toBe("running");
    ticks(2, id);
    await call("POST", `/work-items/${id}/gates/local_review/reject`, { note: "again" });
    ticks(2, id);
    expect(S.bundles[id].sessions.at(-1)).toMatchObject({ node_id: "verification", hook_point: "verification.review.code_review", status: "running" });
  });

  it("a retry forks the run: a gate approved before is asked again", async () => {
    const { S, ticks, call } = started();
    const id = board(S, FAILED);
    const had = S.bundles[id].events.length;
    expect((await call("POST", `/work-items/${id}/review`, { outcome: "request_changes", node: "implementation" })).body.action).toBe("retried");
    expect(S.bundles[id].events.slice(had).map((e) => e.type)).toEqual(["rewind_requested", "work_item_retried", "gate_reopened", "run_forked", "cap_counters_reset", "review_submitted"]);
    for (let k = 0; k < 6 && S.bundles[id].item.status === "active"; k++) ticks(1, id);
    expect(S.bundles[id].item).toMatchObject({ status: "needs_human", pending_gate: "local_review" });
    // A new run: implementation, completed in the intake run, writes its completion again in this one.
    expect(count(S, id, "node_completed", "implementation")).toBe(2);
  });

  it("a gate already approved is passed over when a rewind walks back through it", async () => {
    const { S, ticks, call } = started();
    const id = board(S, RUNNING);
    ticks(2, id);
    await call("POST", `/work-items/${id}/gates/local_review/approve`);
    ticks(1, id);
    expect((await call("POST", `/work-items/${id}/review`, { outcome: "request_changes", node: "work_brief" })).body.action).toBe("queued");
    ticks(1, id);
    expect(S.bundles[id].item.current_node_id).toBe("work_brief");
    expect(ticks(1, id)).toEqual([
      ["worker_session_exited", expect.objectContaining({ status: "done" })],
      ["node_started", { node_id: "merge_request" }],
      ["worker_session_created", expect.objectContaining({ hook_point: "merge_request.rebase.rebase" })],
      ["worker_session_created", expect.objectContaining({ hook_point: "merge_request.open.open_draft" })],
    ]);
  });

  it("a resume after a node skip passes the skipped node", async () => {
    const { S, ticks, call } = started();
    const id = board(S, RUNNING);
    ticks(1, id);
    await call("POST", `/work-items/${id}/skip`, {});
    await call("POST", `/work-items/${id}/pause`);
    await call("POST", `/work-items/${id}/resume`, {});
    expect(ticks(1, id)).toEqual([["node_started", { node_id: "local_review" }], ["gate_requested", { gate: "local_review", node_id: "local_review" }]]);
  });

  it.each([
    ["a pause and resume mid-agent-node", "kraft-91bc", 0],
    ["a pause and resume mid-builtin-node", "kraft-b108", 1],
  ])("%s runs the node again", async (_, bead, before) => {
    const { S, ticks, call } = started();
    const id = board(S, bead);
    if (bead === FAILED) await call("POST", `/work-items/${id}/retry`, {});
    ticks(before, id);
    const node = S.bundles[id].item.current_node_id;
    await call("POST", `/work-items/${id}/pause`);
    await call("POST", `/work-items/${id}/resume`, {});
    const evs = ticks(1, id);
    expect(evs[0]).toEqual(["node_started", { node_id: node }]);
    expect(types(evs)).toContain("worker_session_created");
  });

  it("an already-completed builtin node run again writes node_completed once, and the walk still moves on", async () => {
    const { S, ticks, call } = started();
    const id = board(S, FAILED);
    await call("POST", `/work-items/${id}/retry`, {});
    ticks(2, id);
    // On mr_checks; changes asked at merge_request wait for mr_checks to finish, then take it back there.
    expect((await call("POST", `/work-items/${id}/review`, { outcome: "request_changes", node: "merge_request" })).body.action).toBe("queued");
    ticks(1, id);
    expect(S.bundles[id].item.current_node_id).toBe("merge_request");
    ticks(1, id);
    // Entered three times (the sample's failed run, the retry, the rewind), completed once in this run.
    expect([S.bundles[id].item.current_node_id, count(S, id, "node_completed", "merge_request"), count(S, id, "node_started", "merge_request")]).toEqual(["mr_checks", 1, 3]);
  });

  it("a request for changes made while it runs takes it back to that node once the one it is on completes", async () => {
    const { S, ticks, call } = started();
    const id = board(S, RUNNING);
    const r = await call("POST", `/work-items/${id}/review`, { outcome: "request_changes", node: "implementation", summary: "the cache is unbounded" });
    expect(r.body.action).toBe("queued");
    expect(ticks(1, id).slice(1, 3)).toEqual([["node_completed", { node_id: "verification" }], ["node_started", { node_id: "implementation" }]]);
    // Spent by its target's node_started: on from there, not back again. implementation completed once already.
    expect(types(ticks(1, id))).toEqual(["worker_session_exited", "node_started", "worker_session_created", "worker_session_started"]);
    expect(count(S, id, "node_completed", "implementation")).toBe(1);
    expect(S.bundles[id].item.current_node_id).toBe("verification");
  });
});

describe("the clock and the purse", () => {
  it.each<[string, (it: any) => void, unknown[], Record<string, unknown>]>([
    // kraft-e410 is a rate limit: its poller counts the retry and retries it on its node (`rate_limit_retry.py`).
    ["a rate limit is retried on its node, its count up", () => {}, [
      ["work_item_retried", { node_id: "verification", loop: null, steer: RESUME_PROMPT, escalated: false, seeded: false }],
      ["node_started", { node_id: "verification" }], ["worker_session_created", expect.anything()], ["worker_session_started", expect.anything()],
    ], { status: "active", display_status: "running", retry_at: null, stop: null, rate_limit: null }],
    ["a rate limit past its retries stops for a person", (it) => { it.rate_limit = { count: 5, cap: 5 }; }, [
      ["work_item_needs_human", { node_id: "verification", reason: "rate_limit retries exhausted after 5 attempt(s)", kind: "cap" }],
    ], { status: "needs_human", display_status: "needs_you", stop: expect.objectContaining({ kind: "cap" }), rate_limit: null }],
    // A CI wait is re-entered with no event of its own (`waits.py`, `store.mark_reentered`).
    ["a CI wait is re-entered", (it) => { Object.assign(it, { status: "waiting", stop: { ...it.stop, kind: "wait" } }); }, [
      ["node_started", { node_id: "verification" }], ["worker_session_created", expect.anything()], ["worker_session_started", expect.anything()],
    ], { status: "active", display_status: "running", retry_at: null, stop: null }],
  ])("once its retry time passes, %s", (_, setup, want, item) => {
    let at = NG_NOW;
    const { S, ticks } = started(undefined, () => at);
    const id = board(S, WAITING);
    setup(S.bundles[id].item);
    expect(ticks(2, id)).toEqual([]);
    at = "2026-09-13T10:13:43.000Z";
    expect(ticks(1, id)).toEqual(want);
    expect(S.bundles[id].item).toMatchObject(item);
  });

  it("the rate-limit relaunch leaves a pending rewind waiting: it is taken once the node completes", async () => {
    let at = NG_NOW;
    const { S, ticks, call } = started(undefined, () => at);
    const id = board(S, WAITING);
    expect((await call("POST", `/work-items/${id}/review`, { outcome: "request_changes", node: "implementation" })).body.action).toBe("queued");
    at = "2026-09-13T10:13:43.000Z";
    expect(types(ticks(1, id)).slice(0, 2)).toEqual(["work_item_retried", "node_started"]);
    expect(S.bundles[id].item.current_node_id).toBe("verification");
    expect(ticks(1, id).slice(1, 3)).toEqual([["node_completed", { node_id: "verification" }], ["node_started", { node_id: "implementation" }]]);
  });

  it("stops for a spend cap before the next agent task, and raising it lets it go on", async () => {
    const { S, ticks, call } = started();
    const id = board(S, RUNNING);
    S.bundles[id].item.budget_cap.cap_usd = 2.42;
    const reason = "budget cap reached: $2.45 spent on this work item, cap $2.42. Nothing new was started; a running agent was not interrupted.";
    expect(ticks(1, id).slice(1)).toEqual([
      ["node_completed", { node_id: "verification" }],
      ["node_started", { node_id: "work_brief" }],
      ["work_item_needs_human", { node_id: "work_brief", reason, kind: "budget", budget: { scope: "work_item", spent_usd: 2.45, cap_usd: 2.42 } }],
    ]);
    expect(S.bundles[id].item).toMatchObject({ status: "needs_human", display_status: "needs_you", stop: { kind: "budget", node: "work_brief", reason, scope: "work_item" }, step: null, summary: expect.objectContaining({ step: null }) });
    // The UI offers the item's own Raise budget for it (`budgetRaise`), as it does for the board's sample.
    expect([budgetRaise((await call("GET", `/work-items/${id}`)).body), budgetRaise(S.bundles[board(S, "kraft-58e2")].item)]).toEqual(["item", "item"]);
    expect(ticks(2, id)).toEqual([]);
    expect((await call("POST", `/work-items/${id}/budget/raise`, { budget_usd: 8 })).status).toBe(200);
    expect(types(ticks(1, id))).toEqual(["node_started", "worker_session_created", "worker_session_started"]);
  });

  it("checks the cap before a steer's queued attempt starts too", async () => {
    const { S, ticks, call } = started();
    const id = board(S, RUNNING);
    await call("POST", `/work-items/${id}/pause`);
    await call("POST", `/work-items/${id}/resume`, { steer: "bound it" });
    const queued = S.bundles[id].sessions.at(-1)!;
    S.bundles[id].item.budget_cap.cap_usd = 2.41;
    // Stopped before the attempt launched: it is taken back, and the steer it would have carried reported undelivered.
    expect(ticks(1, id)).toEqual([
      ["node_started", { node_id: "verification" }],
      ["work_item_needs_human", expect.objectContaining({ node_id: "verification", kind: "budget" })],
      ["steer_undelivered", { steer: "bound it" }],
    ]);
    expect(S.bundles[id].item.stop).toMatchObject({ kind: "budget", node: "verification", scope: "work_item" });
    expect(S.bundles[id].sessions.some((s) => s.id === queued.id)).toBe(false);
    // A raise retries it with no steer: a fresh attempt of the node's agent task.
    await call("POST", `/work-items/${id}/budget/raise`, { budget_usd: 8 });
    expect(S.bundles[id].events.filter((e) => e.type === "work_item_retried").at(-1)?.payload.steer).toBeNull();
    expect(types(ticks(1, id))).toEqual(["node_started", "worker_session_created", "worker_session_started"]);
  });
});

describe("an escalation turn", () => {
  it.each([
    // The sample's turn is running when the demo opens; it is stuck on verification, which is not a failure kind.
    ["the escalated sample's", ESCALATED, null, "verification", "needs_you", "The repair rounds keep fixing what code_review reports"],
    ["a visitor's, on a failed item", FAILED, "why does the forge refuse the token?", "merge_request", "failed", "open_draft fails the same way on every attempt"],
  ])("%s ends on its own on the second tick that sees it, as its agent finishing ends it", async (_, bead, message, node, shown, found) => {
    const { S, ticks, call } = started();
    const id = board(S, bead);
    if (message) expect((await call("POST", `/work-items/${id}/escalate`, { message })).status).toBe(200);
    const b = S.bundles[id];
    const s = b.sessions.find((x) => x.hook_point === "escalation" && x.status === "running")!;
    expect(ticks(1, id)).toEqual([]);
    // `store.session_exited`: done, with the usage it reported and the summary it wrote in its thread's folder.
    const ref = `items/${id}/sessions/escalation-1/out/summary.md`;
    expect(ticks(1, id)).toEqual([["worker_session_exited", { session_id: s.id, status: "done", wall_ms: expect.any(Number), model: "claude-sonnet-4-5", tokens_in: 28_000, tokens_cache_write: 0, tokens_cache_read: 0, tokens_out: 3_000, cost_usd: 0.04 }]]);
    expect(s).toMatchObject({ status: "done", exited_at: NG_NOW, session_summary_ref: ref, cost_usd: 0.04 });
    // The item stays as it stopped, its thread ended with its turn.
    expect((await call("GET", `/work-items/${id}`)).body).toMatchObject({ status: "needs_human", display_status: shown, escalation_threads: [{ thread: 1, session_id: s.id, status: "done", ended_at: NG_NOW }] });
    // The indexer files the summary as the item's document, the one the task's Output links by its path.
    const docs = (await call("GET", `/work-items/${id}/documents`)).body.documents;
    expect(docs).toEqual([expect.objectContaining({ path: ref, title: `Escalation reply on ${node}`, kind: "sessions", source_kind: "session_summary", node_id: node, hook_point: "escalation", worker_session_id: s.id, session_status: "done" })]);
    const doc = (await call("GET", `/documents/${docs[0].document_id}`)).body;
    expect([doc.title, doc.metadata]).toEqual([`Escalation reply on ${node}`, {}]);
    for (const words of [`# Escalation reply on ${node}\n`, found, `Retry ${node} when you are ready`]) expect(doc.content).toContain(words);
    // Recent tells it, and the thread takes a reply: the next turn of the same thread, the answered one's summary its own again.
    expect(recent(b.events as any).map((l) => l.line)).toContain("escalation answered");
    expect((await call("POST", `/work-items/${id}/escalate`, { message: "go on" })).status).toBe(200);
    expect([s.session_summary_ref, b.events.filter((e) => e.type === "escalation_message").at(-1)?.payload]).toEqual([`items/${id}/sessions/${s.id}/out/summary.md`, expect.objectContaining({ thread: 1, turn: 2 })]);
    ticks(2, id);
    // That turn's summary is filed at the thread's path again: one document, the newest turn's.
    expect((await call("GET", `/work-items/${id}/documents`)).body.documents).toEqual([expect.objectContaining({ document_id: docs[0].document_id, path: ref, worker_session_id: b.sessions.at(-1)!.id })]);
  });

  it("a turn the visitor stopped (Stop agent) is not ended again", async () => {
    const { S, ticks, call } = started();
    const id = board(S, ESCALATED);
    const s = S.bundles[id].sessions.find((x) => x.hook_point === "escalation" && x.status === "running")!;
    ticks(1, id);
    expect((await call("POST", `/work-items/${id}/escalate/stop`)).status).toBe(200);
    expect(ticks(3, id)).toEqual([]);
    expect([s.status, s.session_summary_ref, S.docs[id]]).toEqual(["paused", null, []]);
  });
});

describe("when the board goes quiet", () => {
  /** Pause the running item; `gate` also ends the one waiting at a gate. */
  const quiet = async (call: ReturnType<typeof started>["call"], S: Scenario, gate: boolean) => {
    for (const bead of [RUNNING]) expect((await call("POST", `/work-items/${board(S, bead)}/pause`)).status).toBe(200);
    if (gate) expect((await call("POST", `/work-items/${board(S, GATED)}/cancel`, { reason: "superseded" })).status).toBe(200);
  };

  it.each([
    // No gate waits once the gated item is cancelled: only the running item holds it back.
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
  it("one step a tick, however often it is resumed while it runs", () => {
    const { S, serve, ticks } = started();
    // The demo's boot: armed when made, then resumed by `whileShown` on a shown tab.
    serve.motion!.resume();
    serve.motion!.resume();
    expect(types(ticks(1, board(S, RUNNING)))).toEqual(["worker_session_exited", "node_completed", "node_started", "worker_session_created", "worker_session_started"]);
  });

  it("a tick that throws still arms the next", () => {
    const { S, ticks } = started();
    const b = S.bundles[board(S, RUNNING)];
    const real = b.sessions;
    let thrown = false;
    Object.defineProperty(b, "sessions", { configurable: true, get: () => { if (!thrown) { thrown = true; throw new Error("boom"); } return real; } });
    expect(() => ticks(1)).toThrow("boom");
    expect(types(ticks(1, b.item.id))).toContain("node_started");
  });

  it("nothing, while paused; it goes on once resumed", () => {
    const { serve, ticks } = started();
    serve.motion!.pause();
    expect(ticks(10)).toEqual([]);
    serve.motion!.resume();
    expect(ticks(1).length).toBeGreaterThan(0);
  });

  it.each([
    ["motion: false", false],
    ["motion left out, as the contract suite calls the mock", "omit"],
  ] as const)("nothing, in a mock with %s, however long it runs: an escalation turn runs on", async (_, motion) => {
    const { S, serve, ticks, call } = started(motion);
    // The sample's turn, and one the visitor starts.
    expect((await call("POST", `/work-items/${board(S, FAILED)}/escalate`, { message: "help" })).status).toBe(200);
    const before = JSON.stringify(S.bundles);
    expect([serve.motion, ticks(20)]).toEqual([null, []]);
    expect(JSON.stringify(S.bundles)).toBe(before);
    expect([ESCALATED, FAILED].map((bead) => S.bundles[board(S, bead)].item.display_status)).toEqual(["escalated", "escalated"]);
  });

  it("a board item that is not active: the escalated row, once its turn answered", () => {
    const { S, ticks } = started();
    const b = S.bundles[board(S, ESCALATED)];
    ticks(2);
    expect([b.item.status, b.item.display_status]).toEqual(["needs_human", "needs_you"]);
    const before = JSON.stringify(b);
    ticks(6);
    expect(JSON.stringify(b)).toBe(before);
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
