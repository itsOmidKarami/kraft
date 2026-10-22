import { describe, expect, it } from "vitest";
import { buildScenario, type ItemBundle, type Scenario } from "../e2e/contract/mocks/fixtures";
import { mockServer, type Answer } from "../e2e/contract/mocks/mockApi";
import { ngDryRun } from "../e2e/contract/mocks/ngBoard";
import { NG_NOW } from "../e2e/contract/mocks/ngItems";
import { recent } from "../src/ng/item/events";
import { displayStatus, escalationThreads, pendingRewind, RESUME_PROMPT, transitions, type Ev, type Transitions } from "../e2e/contract/mocks/transitions";

/** The mock's item transitions (transitions.ts): what each door changes, the events it appends, the words it refuses in. */

const AT = "2026-09-13T10:12:00.000Z";
/** A fresh scenario and its transitions, the clock at AT; `told` holds what `onEvent` was handed. */
const fresh = (mustFix: string[] = []) => {
  const S = buildScenario("default");
  const told: Ev[] = [];
  return { S, told, T: transitions(S, { now: () => AT, mustFix: () => mustFix, onEvent: (e) => told.push(e) }) };
};
/** An item-page scenario's id (`running`, `needs-gate`…), or a board row's by its bead (`kraft-58e2`). */
const idOf = (S: Scenario, at: string) => (at.startsWith("kraft-") ? [...S.ngBoard, ...S.ngArchived].find((id) => S.bundles[id].item.bead_id === at)! : S.ng[at]);

describe("displayStatus", () => {
  it.each([
    ["archived, whatever its status", { status: "completed", archived_at: AT }, "archived"],
    ["a status with its own badge", { status: "paused", pending_gate: "final_review" }, "paused"],
    ["an ended status's badge", { status: "abandoned" }, "cancelled"],
    ["needs_human at a pending gate", { status: "needs_human", pending_gate: "final_review", stop: { kind: "failed" } }, "needs_you"],
    ["needs_human with a live escalation turn", { status: "needs_human", stop: { kind: "failed" }, escalation_threads: [{ status: "running" }] }, "escalated"],
    ["needs_human on a failure kind", { status: "needs_human", stop: { kind: "infra" }, escalation_threads: [{ status: "done" }] }, "failed"],
    ["needs_human on any other stop", { status: "needs_human", stop: { kind: "cap" } }, "needs_you"],
  ])("%s", (_name, item, shown) => {
    expect(displayStatus(item)).toBe(shown);
  });

  it("agrees with every sample item's own badge, and every sample's threads are its sessions'", () => {
    const S = buildScenario("default");
    const off = Object.values(S.bundles).filter((b) => b.item.display_status !== displayStatus(b.item)).map((b) => `${b.item.id} ${b.item.status} ${b.item.display_status}`);
    expect(off).toEqual([]);
    // Every item that carries threads (the legacy board's items, from before the field, carry none).
    const carrying = Object.values(S.bundles).filter((b) => "escalation_threads" in b.item);
    expect(carrying.length).toBe(Object.values(S.ng).length + S.ngBoard.length + S.ngArchived.length);
    const apart = carrying.filter((b) => JSON.stringify(b.item.escalation_threads) !== JSON.stringify(escalationThreads(b.sessions))).map((b) => b.item.id);
    expect(apart).toEqual([]);
    expect(S.bundles[S.ng.escalated].item.display_status).toBe("escalated");
  });
});

/** An event a door appends: its type, the payload fields it carries, and the node it is filed under. */
type Want = [type: string, payload?: Record<string, unknown>, node?: string | null];
type Act = (T: Transitions, id: string, b: ItemBundle) => { status: number };
const noRejectTo: Act = (T, id, b) => {
  // A copy: the sample items share one node list.
  const cd = b.item.chain_definition;
  b.item.chain_definition = { ...cd, nodes: cd.nodes.map((n: any) => (n.id === "final_review" ? { ...n, reject_to: null } : n)) };
  return T.reject(id, "final_review", "redo it");
};

/** What a retry's run fork writes after its `work_item_retried` (`store.forks.fork_run`): each approved gate from the
 *  retried node on reopened, the fork, and the counters a person's retry resets. */
const forked = (...gates: string[]): Want[] => [
  ...gates.map((gate): Want => ["gate_reopened", { gate, reason: "retry" }, gate]),
  ["run_forked", { scope: "node", reopened: gates, preserved: [] }],
  ["cap_counters_reset", { by: "human", counters: {} }],
];

/** What an escalation turn's start writes (`escalate.dispatch`): its message, then its session, born and launched. */
const turnStarted = (message: Record<string, unknown> = {}, node?: string, attempt = 1): Want[] => [
  ["escalation_message", message, node],
  ["worker_session_created", { hook_point: "escalation", round: 0, attempt, thread: message.thread ?? 1 }, node],
  ["worker_session_started", { hook_point: "escalation", round: 0, attempt, thread: message.thread ?? 1 }, node],
];

describe("transitions", () => {
  it.each<[string, string, Act, Record<string, unknown>, Want[]]>([
    ["approve clears the gate and runs on", "needs-gate", (T, id) => T.approve(id, "final_review"), { status: "active", display_status: "running", pending_gate: null, stop: null },
      [["gate_approved", { gate: "final_review", by: "human" }, "final_review"], ["node_completed", { node_id: "final_review" }, "final_review"]]],
    ["reject goes back to the gate's reject_to", "needs-gate", (T, id) => T.reject(id, "final_review", "too vague"), { status: "active", display_status: "running", pending_gate: null, current_node_id: "implementation", attempts: [] },
      [["gate_rejected", { gate: "final_review", note: "too vague", node: "implementation", by: "human", verdict: "reject" }, "implementation"]]],
    ["reject with no reject_to goes back to the exec node before the gate", "needs-gate", noRejectTo, { status: "active", current_node_id: "summary" },
      [["gate_rejected", { node: "summary" }, "summary"]]],
    ["pause stops the node's running session", "running", (T, id) => T.pause(id), { status: "paused", display_status: "paused" },
      [["pause_requested", { sessions: [expect.any(String)] }, null], ["worker_session_paused", { session_id: expect.any(String) }, null]]],
    ["resume runs a paused item again, its steer taken", "paused", (T, id, b) => ((b.item.pending_steer_context = "look at the race"), T.resume(id)), { status: "active", display_status: "running", current_node_id: "verification", pending_steer_context: null },
      [["work_item_resumed", { steer: "look at the race" }, null]]],
    ["resume with a steer of its own replaces a queued one", "paused", (T, id, b) => ((b.item.pending_steer_context = "look at the race"), T.resume(id, "look at the lock")), { pending_steer_context: null },
      [["work_item_resumed", { steer: "look at the lock" }, null]]],
    ["a rewind is used up by the resume that honoured it", "paused", (T, id) => (T.requestChanges(id, "implementation", "redo the cache"), T.resume(id), T.pause(id), T.resume(id)), { status: "active", current_node_id: "implementation" },
      [["rewind_requested"], ["work_item_resumed", { steer: "redo the cache" }], ["pause_requested"], ["work_item_resumed", { steer: null }]]],
    ["a rewind is used up by the retry that honoured it", "failed", (T, id, b) => (T.append(id, "rewind_requested", { review_id: "r1", target: "implementation", note: "redo it" }), T.retry(id), Object.assign(b.item, { status: "needs_human", current_node_id: "verification" }), T.retry(id)), { status: "active", current_node_id: "verification" },
      [["rewind_requested"], ["work_item_retried", { node_id: "implementation", steer: "redo it" }], ...forked("local_review"), ["work_item_retried", { node_id: "verification", steer: null }], ...forked()]],
    ["resume starts a never-started item at its first node", "kraft-f5d3", (T, id) => T.resume(id), { status: "active", display_status: "running", current_node_id: "spec" }, [["work_item_resumed", { steer: null }, null]]],
    ["resume honours a queued rewind, its note carried with the steer", "paused", (T, id) => (T.requestChanges(id, "implementation", "redo the cache"), T.resume(id, "and bound it")), { status: "active", current_node_id: "implementation" },
      [["rewind_requested", { target: "implementation", note: "redo the cache" }, "implementation"], ["work_item_resumed", { steer: "redo the cache\n\nand bound it" }, null]]],
    ["retry honours a pending rewind", "failed", (T, id) => (T.append(id, "rewind_requested", { review_id: "r1", target: "implementation", note: "redo it" }, "implementation"), T.retry(id)), { status: "active", current_node_id: "implementation" },
      [["rewind_requested"], ["work_item_retried", { node_id: "implementation", steer: "redo it" }, "implementation"], ...forked("local_review")]],
    ["a cancelled rewind is not honoured", "failed", (T, id) => (T.append(id, "rewind_requested", { review_id: "r1", target: "implementation", note: "redo it" }), T.append(id, "rewind_cancelled", { review_id: "r1" }), T.retry(id)), { current_node_id: "merge_request" },
      [["rewind_requested"], ["rewind_cancelled"], ["work_item_retried", { node_id: "merge_request", steer: null }], ...forked()]],
    ["escalate, retry, pause and resume: the retry stops the turn", "failed", (T, id) => (T.escalate(id, "look"), T.retry(id), T.pause(id), T.resume(id)), { status: "active", display_status: "running", escalation_threads: [expect.objectContaining({ status: "paused" })] },
      [...turnStarted(), ["worker_session_paused"], ["work_item_retried"], ...forked(), ["pause_requested", { sessions: [] }], ["work_item_resumed"]]],
    ["escalate at a gate, approve, pause and resume: the approval stops the turn", "needs-gate", (T, id) => (T.escalate(id, "look"), T.approve(id, "final_review"), T.pause(id), T.resume(id)), { status: "active", display_status: "running", escalation_threads: [expect.objectContaining({ status: "paused" })] },
      [...turnStarted(), ["pause_requested", { sessions: [expect.any(String)] }], ["worker_session_paused"], ["gate_approved"], ["node_completed"], ["pause_requested", { sessions: [] }], ["work_item_resumed"]]],
    ["escalate at a gate, then reject: the rejection stops the turn", "needs-gate", (T, id) => (T.escalate(id, "look"), T.reject(id, "final_review", "no")), { status: "active", escalation_threads: [expect.objectContaining({ status: "paused" })] },
      [...turnStarted(), ["pause_requested"], ["worker_session_paused"], ["gate_rejected"]]],
    ["a thread no session stands for holds nothing up, and goes", "paused", (T, id, b) => ((b.item.escalation_threads = [{ thread: 1, session_id: "f".repeat(32), turns: 1, status: "running", ended_at: null }]), T.resume(id)), { status: "active", escalation_threads: [] },
      [["work_item_resumed"]]],
    ["an end stops an escalation turn on another node too", "failed", (T, id, b) => (T.escalate(id, "look"), (b.item.current_node_id = "summary"), T.end(id, "cancel", "dup")), { status: "abandoned", escalation_threads: [expect.objectContaining({ status: "paused" })] },
      [...turnStarted(), ["worker_session_paused"], ["work_item_cancelled", { node_id: "summary" }], ["work_item_abandoned"]]],
    ["abandon twice reclaims again with no second event", "failed", (T, id) => (T.abandon(id), T.abandon(id)), { status: "abandoned" }, [["work_item_abandoned", {}, null]]],
    ["retry runs a stopped item on its node", "failed", (T, id) => T.retry(id, "use the new token"), { status: "active", display_status: "running", stop: null, current_node_id: "merge_request" },
      [["work_item_retried", { node_id: "merge_request", steer: "use the new token", escalated: false }, "merge_request"], ...forked()]],
    ["skip passes the node it stands on", "failed", (T, id) => T.skip(id, "merge_request", "opened by hand"), { status: "active", display_status: "running", stop: null },
      [["node_skipped", { node_id: "merge_request", gate: null, note: "opened by hand" }, "merge_request"]]],
    ["skip passes a pending gate", "needs-gate", (T, id) => T.skip(id, "final_review"), { status: "active", display_status: "running", pending_gate: null },
      [["node_skipped", { node_id: "final_review", gate: "final_review", note: null }, "final_review"]]],
    ["skip passes one task by path", "failed", (T, id) => T.skip(id, "merge_request.open.open_draft"), { status: "active", display_status: "running" },
      [["scope_skipped", { path: "merge_request.open.open_draft", note: null }, null]]],
    ["cancel ends it abandoned, its session stopped", "running", (T, id) => T.end(id, "cancel", "superseded"), { status: "abandoned", display_status: "cancelled" },
      [["worker_session_paused", {}, null], ["work_item_cancelled", { reason: "superseded", node_id: "verification" }, "verification"], ["work_item_abandoned", {}, null]]],
    ["complete ends it completed", "running", (T, id) => T.end(id, "complete", "landed elsewhere"), { status: "completed", display_status: "done" },
      [["worker_session_paused", {}, null], ["work_item_manually_completed", { reason: "landed elsewhere", node_id: "verification" }, "verification"], ["work_item_completed", {}, null]]],
    ["abandon ends a stopped item", "failed", (T, id) => T.abandon(id), { status: "abandoned", display_status: "cancelled" }, [["work_item_abandoned", {}, null]]],
    ["archive files an ended item away", "kraft-3a90", (T, id) => T.archive(id), { status: "completed", display_status: "archived", archived_by: "you", archived_at: AT },
      [["work_item_archived", { by: "you" }, null]]],
    ["restore puts it back under Done", "kraft-9a02", (T, id) => T.restore(id), { status: "completed", display_status: "done", archived_at: null }, [["work_item_restored", {}, null]]],
    ["raise budget lifts the cap and retries", "kraft-58e2", (T, id) => T.raiseBudget(id, 8), { status: "active", display_status: "running", stop: null, budget_cap: expect.objectContaining({ cap_usd: 8, source: "item" }) },
      [["budget_raised", { budget_usd: 8 }, null], ["work_item_retried", { node_id: "verification" }, "verification"], ...forked()]],
    ["escalate starts a turn in a new thread", "failed", (T, id) => T.escalate(id, "look at the token", true), { status: "needs_human", display_status: "escalated" },
      turnStarted({ message: "look at the token", auto: false, thread: 1, turn: 1 }, "merge_request")],
    ["stop escalation ends the turn, the item as it stopped", "failed", (T, id) => (T.escalate(id, "look"), T.stopEscalation(id)), { status: "needs_human", display_status: "failed" },
      [...turnStarted({ turn: 1 }, "merge_request"), ["worker_session_paused", { session_id: expect.any(String) }, null]]],
    ["a stopped escalation takes the next turn", "failed", (T, id) => (T.escalate(id, "look"), T.stopEscalation(id), T.escalate(id, "again")), { display_status: "escalated" },
      [...turnStarted({ turn: 1 }), ["worker_session_paused"], ...turnStarted({ message: "again", thread: 1, turn: 2 }, undefined, 2)]],
    ["a title edit", "running", (T, id) => T.patch(id, { title: "Cache embeddings by hash" }), { title: "Cache embeddings by hash", display_status: "running" },
      [["work_item_title_edited", { title: "Cache embeddings by hash" }, null]]],
    ["a description edit", "running", (T, id) => T.patch(id, { description: "Bound the cache." }), { description: "Bound the cache." },
      [["work_item_description_edited", { description: "Bound the cache." }, null]]],
  ])("%s", (_name, at, act, after, want) => {
    const { S, T, told } = fresh();
    const id = idOf(S, at);
    const b = S.bundles[id];
    const had = b.events.length;
    const from = T.cursor();
    expect(act(T, id, b).status).toBeLessThan(300);
    expect(b.item).toMatchObject({ ...after, updated_at: AT });
    const added = b.events.slice(had);
    expect(added).toMatchObject(want.map(([type, payload = {}, node]) => ({ type, payload, ...(node !== undefined ? { node_id: node } : {}) })));
    // Each with the next seq, in order, at the clock's instant, and each handed to onEvent.
    expect(added.map((e) => [e.seq, e.work_item_id, e.created_at])).toEqual(want.map((_, k) => [from + k + 1, id, AT]));
    expect(T.cursor()).toBe(from + want.length);
    expect(told).toEqual(added);
  });

  it.each([
    ["a never-started item: no node yet", "kraft-f5d3", (T: Transitions, id: string) => T.resume(id), { node_id: null, steer: null }],
    ["a pending rewind: the node it was paused on", "paused", (T: Transitions, id: string) => (T.requestChanges(id, "implementation", "redo"), T.resume(id)), { node_id: "verification", steer: "redo" }],
  ])("resume answers the node as it read before the claim: %s", (_name, at, act, body) => {
    const { S, T } = fresh();
    expect(act(T, idOf(S, at)).body).toMatchObject(body);
  });

  it("resume with a steer queues the node's own next attempt, under an id of its own", () => {
    const { S, T } = fresh();
    const id = S.ng.paused;
    const b = S.bundles[id];
    T.escalate(id, "look");
    T.stopEscalation(id);
    const ids = new Set(Object.values(S.bundles).flatMap((x) => x.sessions.map((s) => s.id)));
    const task = b.sessions.filter((s) => s.node_id === "verification" && s.hook_point !== "escalation").at(-1);
    T.resume(id, "look at the race");
    expect(b.events.at(-1)).toMatchObject({ type: "work_item_resumed", payload: { steer: "look at the race" } });
    const s = b.sessions.at(-1);
    expect(s).toMatchObject({ node_id: "verification", hook_point: task.hook_point, attempt: task.attempt + 1, status: "pending", exited_at: null, thread: 1 });
    expect(ids.has(s.id)).toBe(false);
  });

  it("escalate runs a turn of its own, with a log", () => {
    const { S, T } = fresh();
    const id = S.ng.paused;
    const b = S.bundles[id];
    T.escalate(id, "look at the race");
    const s = b.sessions.at(-1);
    expect(s).toMatchObject({ hook_point: "escalation", status: "running", node_id: "verification", exited_at: null });
    expect(b.item.escalation_threads.at(-1)).toMatchObject({ session_id: s.id, status: "running", ended_at: null });
    expect(b.logs[s.id].length).toBeGreaterThan(0);
  });

  it("stop escalation pauses the turn's session and ends its thread", () => {
    const { S, T } = fresh();
    const id = S.ng.failed;
    const b = S.bundles[id];
    T.escalate(id, "look");
    const sid = b.sessions.at(-1).id;
    expect(T.stopEscalation(id)).toEqual({ status: 200, body: { id, session_id: sid, status: "paused" } });
    expect([b.sessions.at(-1), b.item.escalation_threads.at(-1)]).toMatchObject([{ id: sid, status: "paused", exited_at: AT }, { session_id: sid, status: "paused", ended_at: AT }]);
  });

  it.each([
    ["archive", "kraft-3a90", (T: Transitions, id: string) => T.archive(id), "ngArchived", 0],
    ["restore", "kraft-9a02", (T: Transitions, id: string) => T.restore(id), "ngBoard", -1],
  ] as const)("%s moves the row to the other list", (_name, at, act, into, where) => {
    const { S, T } = fresh();
    const id = idOf(S, at);
    act(T, id);
    const other = into === "ngBoard" ? S.ngArchived : S.ngBoard;
    expect([S[into].at(where), other.includes(id)]).toEqual([id, false]);
  });
});

describe("the rate-limit poller's count", () => {
  /** `n` relaunches the poller wrote on `node`. */
  const relaunched = (T: Transitions, id: string, n: number, node = "verification") => {
    for (let k = 0; k < n; k++) T.append(id, "work_item_retried", { node_id: node, loop: null, steer: RESUME_PROMPT, escalated: false, seeded: false });
  };
  const stopped = (n: number) => ({ type: "work_item_needs_human", payload: { node_id: "verification", reason: `rate_limit retries exhausted after ${n} attempt(s)`, kind: "cap" } });
  // Each row's cap is the count it should read, so the poll stops and its reason says the count.
  it.each<[string, (T: Transitions, id: string) => void, number]>([
    // kraft-e410's row says 2 of 5: the two relaunches its events hold.
    ["the sample's two relaunches", () => {}, 2],
    ["more relaunches on its node, read off its events", (T, id) => relaunched(T, id, 3), 5],
    // The counter is never reset: a stop past the retries is a bump too, and a person's retry is none.
    ["a stop past its retries, then a person's retry", (T, id) => {
      relaunched(T, id, 3);
      T.append(id, "work_item_needs_human", { node_id: "verification", reason: "rate_limit retries exhausted after 5 attempt(s)", kind: "cap" });
      T.append(id, "work_item_retried", { node_id: "verification", loop: null, steer: "try again", escalated: false, seeded: false });
    }, 6],
    ["another node's relaunches", (T, id) => relaunched(T, id, 5, "implementation"), 2],
  ])("%s", (_name, setup, count) => {
    const { S, T } = fresh();
    const id = idOf(S, "kraft-e410");
    const it = S.bundles[id].item;
    expect(it.rate_limit).toEqual({ count: 2, cap: 5 });
    setup(T, id);
    it.rate_limit.cap = count;
    expect(T.rateLimitRetry(id).status).toBe(200);
    expect(S.bundles[id].events.at(-1)).toMatchObject(stopped(count));
    // Shown only while it is rate limited.
    expect(it.rate_limit).toBeNull();
  });

  it("goes up by one a poll, so the sample stops on the fourth: past its 2 of 5", () => {
    const { S, T } = fresh();
    const id = idOf(S, "kraft-e410");
    const it = S.bundles[id].item;
    const polls: string[] = [];
    for (let k = 0; k < 6 && it.status !== "needs_human"; k++) {
      // Limited again on the same node, as the relaunch it was given would be.
      Object.assign(it, { status: "rate_limited", retry_at: AT });
      T.rateLimitRetry(id);
      polls.push(S.bundles[id].events.at(-1)!.type);
    }
    expect(polls).toEqual(["work_item_retried", "work_item_retried", "work_item_retried", "work_item_needs_human"]);
    expect(S.bundles[id].events.at(-1)).toMatchObject(stopped(5));
  });
});

describe("refusals", () => {
  const escalated = { first: (T: Transitions, id: string) => void T.escalate(id, "one") };
  /** What a row sets up before the door it refuses: open must-fix threads, or a first action. */
  type Setup = { mustFix?: string[]; first?: (T: Transitions, id: string) => void };
  const cases: [string, string, (T: Transitions, id: string) => { status: number; body: any }, number, string, Setup?][] = [
    ["approve, no gate pending", "running", (T, id) => T.approve(id, "final_review"), 409, "gate 'final_review' is not pending"],
    ["approve, a gate the chain does not have", "needs-gate", (T, id) => T.approve(id, "nope"), 404, "unknown gate 'nope'"],
    ["approve, a node that is not a gate", "needs-gate", (T, id) => T.approve(id, "verification"), 404, "unknown gate 'verification'"],
    ["approve, an open must-fix", "needs-gate", (T, id) => T.approve(id, "final_review"), 409, "must-fix review threads are not resolved: th-must, th-two", { mustFix: ["th-must", "th-two"] }],
    ["approve, an ended item", "done", (T, id) => T.approve(id, "final_review"), 409, "work item is completed; its chain does not run again"],
    ["reject forward, past the gate", "needs-gate", (T, id) => T.reject(id, "final_review", "no", "release"), 400, "cannot reject to 'release': not a node of this chain before 'final_review'"],
    ["pause, a paused item", "paused", (T, id) => T.pause(id), 409, "work item is paused, not running"],
    ["resume, a running item", "running", (T, id) => T.resume(id), 409, "work item is active: it is running, so pause it first"],
    ["resume, at a gate", "needs-gate", (T, id) => T.resume(id), 409, "work item is waiting at its final_review gate, not paused: approve or reject it (kraft item approve ID, or kraft item reject ID --note ...)"],
    ["resume, a stopped item", "failed", (T, id) => T.resume(id), 409, "work item is needs_human, not paused: it stopped, so retry it instead (kraft item retry ID, which takes --steer too)"],
    ["resume, an escalation turn running", "paused", (T, id) => T.resume(id), 409, "an escalation turn (SESSION) is already running", escalated],
    ["retry, a paused item", "paused", (T, id) => T.retry(id), 409, "work item is paused, not stopped: resume it instead, or skip what it would run"],
    ["retry, a running item", "running", (T, id) => T.retry(id), 409, "work item is not stopped"],
    ["retry, never started", "kraft-f5d3", (T, id) => T.retry(id), 409, "work item has no current node to retry"],
    ["skip, an ended item", "done", (T, id) => T.skip(id, "release"), 409, "work item is completed, cannot skip"],
    ["skip, another node", "failed", (T, id) => T.skip(id, "release"), 409, "path: 'release' is not the node the item stands on ('merge_request') or its pending gate"],
    ["skip, an escalation turn running", "failed", (T, id) => T.skip(id, "merge_request"), 409, "an escalation turn (SESSION) is already running", escalated],
    ["cancel, no reason", "running", (T, id) => T.end(id, "cancel", "  "), 422, "reason: a terminal action needs a reason"],
    ["cancel, an ended item", "done", (T, id) => T.end(id, "cancel", "dup"), 409, "work item is already completed"],
    ["abandon, a running item", "running", (T, id) => T.abandon(id), 409, "work item is active; pause it before abandoning"],
    ["archive, a running item", "running", (T, id) => T.archive(id), 409, "only a completed or abandoned item can be archived"],
    ["restore, an item not archived", "done", (T, id) => T.restore(id), 409, "work item is not archived"],
    ["raise budget, a running item", "running", (T, id) => T.raiseBudget(id, 9), 409, "work item is not stopped"],
    ["raise budget, not a spend stop", "failed", (T, id) => T.raiseBudget(id, 9), 409, "work item was not stopped by a spend cap"],
    ["raise budget, under what it spent", "kraft-58e2", (T, id) => T.raiseBudget(id, 2), 422, "the item has already spent $5; a cap of $2 would stop it again at once: raise it above that, or to none"],
    ["escalate, a running item", "running", (T, id) => T.escalate(id, "help"), 409, "work item is not needs_human or paused"],
    ["escalate, never started", "kraft-f5d3", (T, id) => T.escalate(id, "help"), 409, "work item has not started"],
    ["escalate, a spend stop", "kraft-58e2", (T, id) => T.escalate(id, "help"), 409, "a spend cap stopped this item, and an escalation's agent would hit it too: raise it with `kraft item raise-budget ID --usd N` where that takes it (`kraft view show ID` names the cap), then escalate"],
    ["escalate, no message", "failed", (T, id) => T.escalate(id, " "), 400, "message is required"],
    ["escalate, a turn already running", "failed", (T, id) => T.escalate(id, "two"), 409, "an escalation turn (SESSION) is already running", escalated],
    ["stop escalation, no turn running", "failed", (T, id) => T.stopEscalation(id), 409, "no escalation turn is running"],
    ["stop escalation, after a cancel stopped the turn", "failed", (T, id) => T.stopEscalation(id), 409, "no escalation turn is running", { first: (T, id) => void (T.escalate(id, "one"), T.end(id, "cancel", "dup")) }],
    ["stop escalation, a turn already stopped", "failed", (T, id) => T.stopEscalation(id), 409, "no escalation turn is running", { first: (T, id) => void (T.escalate(id, "one"), T.stopEscalation(id)) }],
    ["patch, an empty title", "running", (T, id) => T.patch(id, { title: " " }), 422, "title cannot be empty"],
    ["patch, nothing to patch", "running", (T, id) => T.patch(id, {}), 422, "nothing to patch: send a title, description, chain, agent_overrides, node_overrides, policy, attachments, or budget_usd"],
    ["any door, an unknown item", "running", (T) => T.pause("f".repeat(32)), 404, "unknown work item"],
  ];
  it.each(cases)("%s", (_name, at, act, status, detail, setup = {}) => {
    const { S, T } = fresh(setup.mustFix);
    const id = idOf(S, at);
    setup.first?.(T, id);
    const b = S.bundles[id];
    const before = JSON.stringify([b.item, b.sessions]);
    const had = b.events.length;
    const r = act(T, id);
    const sid = b.sessions.filter((s) => s.hook_point === "escalation").at(-1)?.id;
    expect([r.status, r.body.detail]).toEqual([status, detail.replaceAll("ID", id).replace("SESSION", sid)]);
    // A refusal changes nothing.
    expect([JSON.stringify([b.item, b.sessions]), b.events.length]).toEqual([before, had]);
  });
});

/** The mock on the wire, as the demo and the contract suite put it there. */
const server = (variant: "default" | "empty" = "default") => {
  const S = buildScenario(variant);
  const told: Ev[] = [];
  const serve = mockServer(S, { ngBoard: true, now: () => NG_NOW, onEvent: (e) => told.push(e) });
  const call = async (method: string, path: string, body?: unknown) => {
    const a = (await serve({ method, url: `http://x/api${path}`, json: () => body ?? null })) as Answer;
    return { status: a.status, body: a.body ? JSON.parse(a.body) : null };
  };
  return { S, call, told };
};

describe("the list and the detail", () => {
  it.each([
    ["pause", "kraft-91bc", "pause", {}, "paused", false],
    ["approve at the gate", "kraft-cb59", "gates/final_review/approve", {}, "running", false],
    ["reject at the gate", "kraft-cb59", "gates/final_review/reject", { note: "no" }, "running", false, "implementation"],
    ["a review's request changes at the gate", "kraft-cb59", "gates/final_review/review", { outcome: "request_changes" }, "running", false, "implementation"],
    ["resume", "kraft-4e19", "resume", { steer: "go on" }, "running", false],
    ["retry", "kraft-b108", "retry", {}, "running", false],
    ["cancel", "kraft-91bc", "cancel", { reason: "dup" }, "cancelled", false],
    ["escalate", "kraft-b108", "escalate", { message: "help" }, "escalated", false],
    ["stop escalation", "kraft-b108", "escalate/stop", {}, "failed", false],
    ["archive", "kraft-3a90", "archive", {}, "archived", true],
    ["a bulk pause", "kraft-91bc", "../bulk", { action: "pause", ids: ["ID"] }, "paused", false],
  ] as [string, string, string, object, string, boolean, string?][])("agree after %s", async (_name, bead, door, body, shown, archived, node) => {
    const { S, call } = server();
    const id = idOf(S, bead);
    if (door === "escalate/stop") await call("POST", `/work-items/${id}/escalate`, { message: "help" });
    const at = S.bundles[id].item.current_node_id;
    // The board has read its list before the action, as it does on boot.
    expect((await call("GET", "/work-items")).body.items.some((i: any) => i.id === id)).toBe(true);
    const sent = JSON.parse(JSON.stringify(body).replace("ID", id));
    const r = await call("POST", door.startsWith("../") ? `/work-items/${door.slice(3)}` : `/work-items/${id}/${door}`, sent);
    expect(r.status, JSON.stringify(r.body)).toBeLessThan(300);
    const list = await call("GET", `/work-items${archived ? "?archived=true" : ""}`);
    const row = list.body.items.find((i: any) => i.id === id);
    const detail = (await call("GET", `/work-items/${id}`)).body;
    expect([row.display_status, row.status, row.updated_at, row.current_node_id]).toEqual([shown, detail.status, NG_NOW, node ?? at]);
    expect([detail.display_status, detail.current_node_id]).toEqual([row.display_status, row.current_node_id]);
  });

  it.each([
    ["pause", "kraft-4e19", ["kraft-91bc"], {}, [{ ok: true, status: "paused" }, { ok: false, status: "paused", error: "work item is paused, not running" }]],
    ["cancel", "kraft-3a90", ["kraft-91bc"], { reason: "dup" }, [{ ok: true, status: "abandoned" }, { ok: false, status: "completed", error: "work item is already completed" }]],
    ["archive", "kraft-91bc", ["kraft-3a90"], {}, [{ ok: true, status: "completed" }, { ok: false, status: "active", error: "only a completed or abandoned item can be archived" }]],
    ["restore", "kraft-3a90", ["kraft-9a02"], {}, [{ ok: true, status: "completed" }, { ok: false, status: "completed", error: "work item is not archived" }]],
  ])("a bulk %s answers each id through its own door", async (action, refused, [taken], more, [yes, no]) => {
    const { S, call } = server();
    const [a, b] = [idOf(S, taken), idOf(S, refused)];
    const r = await call("POST", "/work-items/bulk", { action, ids: [a, b, "nope"], ...more });
    expect(r.body.results).toEqual([{ id: a, ...yes }, { id: b, ...no }, { id: "nope", ok: false, error: "not found" }]);
    const shown = { pause: "paused", cancel: "cancelled", archive: "archived", restore: "done" }[action];
    const lists = [...(await call("GET", "/work-items")).body.items, ...(await call("GET", "/work-items?archived=true")).body.items];
    expect(lists.find((i: any) => i.id === a).display_status).toBe(shown);
  });
});

describe("the review doors", () => {
  it.each([
    ["a stopped item retries at the target", "kraft-b108", "implementation", "retried", "active", ["rewind_requested", "work_item_retried", "gate_reopened", "run_forked", "cap_counters_reset", "review_submitted"], "implementation"],
    ["a running item at the target reruns it", "kraft-91bc", "verification", "rerun", "active", ["rewind_requested", "pause_requested", "worker_session_paused", "work_item_resumed", "review_submitted"], "verification"],
    ["a paused item waits for it", "kraft-4e19", "implementation", "queued", "paused", ["rewind_requested", "review_submitted"], "verification"],
  ])("with no gate pending, request changes: %s", async (_name, bead, node, action, status, types, at) => {
    const { S, call } = server();
    const id = idOf(S, bead);
    const had = S.bundles[id].events.length;
    const r = await call("POST", `/work-items/${id}/review`, { outcome: "request_changes", summary: "redo the cache", node });
    expect(r.body).toMatchObject({ outcome: "request_changes", gate: null, target: node, action });
    const added = S.bundles[id].events.slice(had);
    expect([S.bundles[id].item.status, S.bundles[id].item.current_node_id, added.map((e) => e.type)]).toEqual([status, at, types]);
    expect([added[0].payload, added[0].node_id]).toEqual([{ review_id: "r2", target: node, note: "redo the cache" }, node]);
  });

  it("each rewind has its own review id, so cancelling one leaves a later one pending", () => {
    const { S, T } = fresh();
    const id = S.ng.paused;
    T.requestChanges(id, "spec", "first");
    T.requestChanges(id, "implementation", "second");
    const ids = S.bundles[id].events.filter((e) => e.type === "rewind_requested").map((e) => e.payload.review_id);
    expect(ids).toEqual(["r2", "r3"]);
    T.append(id, "rewind_cancelled", { review_id: "r2" });
    expect(pendingRewind(S.bundles[id])).toMatchObject({ target: "implementation", review_id: "r3" });
  });

  it("a comment publishes the drafts, the gate staying open", async () => {
    const { S, call } = server();
    const id = idOf(S, "kraft-cb59");
    await call("POST", `/work-items/${id}/threads`, { body: "why this bound?", file_path: "search/cache.py", side: "new", start_line: 3, end_line: 3 });
    expect((await call("GET", `/work-items/${id}/threads`)).body.some((t: any) => t.draft)).toBe(true);
    const r = await call("POST", `/work-items/${id}/review`, { outcome: "comment" });
    expect(r.body).toEqual({ review_id: "r2", outcome: "comment", gate: "final_review", reply_agent: true });
    expect((await call("GET", `/work-items/${id}/threads`)).body.some((t: any) => t.draft || t.comments.some((c: any) => c.draft))).toBe(false);
    expect([S.bundles[id].events.at(-1), S.bundles[id].item.pending_gate]).toMatchObject([{ type: "review_submitted", payload: { gate: "final_review", outcome: "comment" }, node_id: "final_review" }, "final_review"]);
  });

  it.each([
    ["an ended item", "kraft-3a90", "/review", { outcome: "comment" }, 409, "work item is completed; its chain does not run again"],
    ["a gate the chain does not have", "kraft-cb59", "/gates/nope/review", { outcome: "approve" }, 404, "unknown gate 'nope'"],
    ["approve with no gate pending", "kraft-b108", "/review", { outcome: "approve" }, 409, "nothing to approve: no gate is pending"],
  ])("refuse %s", async (_name, bead, door, body, status, detail) => {
    const { S, call } = server();
    const id = idOf(S, bead);
    const had = S.bundles[id].events.length;
    expect(await call("POST", `/work-items/${id}${door}`, body)).toEqual({ status, body: { detail } });
    expect(S.bundles[id].events.length).toBe(had);
  });
});

describe("samples and answers as the server shapes them", () => {
  it("an item that ran and lost its worktree answers its diff and its comparison with the 404", async () => {
    const { S, call } = server();
    const id = idOf(S, "kraft-91bc");
    S.bundles[id].item.worktree_exists = false;
    for (const route of ["diff", "compare"]) expect(await call("GET", `/work-items/${id}/${route}`)).toEqual({ status: 404, body: { detail: "this work item's worktree was removed from disk" } });
  });

  it("a node retry answers the node as its path", async () => {
    const { S, call } = server();
    expect((await call("POST", `/work-items/${idOf(S, "kraft-b108")}/retry`, {})).body).toMatchObject({ node_id: "merge_request", path: "merge_request" });
  });

  it("the spend-capped sample says so in its stop, its last event and its spend", () => {
    const { S } = server();
    const b = S.bundles[idOf(S, "kraft-58e2")];
    expect(b.item.stop).toMatchObject({ kind: "budget", scope: "work_item" });
    expect(b.item.stop.facts).toEqual({});
    expect(b.events.filter((e) => e.type === "work_item_needs_human").at(-1)?.payload).toEqual({ node_id: "verification", reason: "Spend cap reached · $5.00 of $5.00", kind: "budget", budget: { scope: "work_item", spent_usd: 5, cap_usd: 5 } });
    const cost = b.sessions.reduce((a, s) => a + s.cost_usd, 0);
    expect([Math.abs(cost - 5) < 0.01, Math.abs(b.item.usage.total.cost_usd - 5) < 0.01, b.item.budget_cap.spent_usd]).toEqual([true, true, 5]);
    // A spend cap interrupts nothing: the session that spent the last of it ran to its end, then the walk stopped.
    expect(b.sessions.filter((s) => s.status !== "done")).toEqual([]);
    const last = b.sessions.at(-1);
    expect([last.hook_point, last.attempt, last.session_summary_ref, b.item.stop.task, b.item.stop.attempt]).toEqual(["verification.review.code_review", 2, `.engineering/sessions/${last.id}.md`, "verification.review.code_review", 2]);
  });

  it.each([
    // A time cap stops its session (`session_exited`, capped_out); a spend cap, a stuck fix loop and a question let the
    // attempt finish first. Each exit reports the agent's usage.
    ["kraft-7d21, stopped by its running-time cap", "kraft-7d21", "capped_out"],
    ["kraft-58e2, stopped by its spend cap", "kraft-58e2", "done"],
    ["kraft-2c77, stuck, its fix loop making no progress", "kraft-2c77", "done"],
    ["kraft-a3f0, stopped on its agent's question", "kraft-a3f0", "done"],
  ])("%s: its last attempt's exit is on the timeline before the stop, and Recent has it ended", (_name, bead, status) => {
    const { S } = server();
    const b = S.bundles[idOf(S, bead)];
    const last = b.sessions.filter((s) => s.hook_point === "verification.review.code_review").at(-1);
    const exit = b.events.find((e) => e.type === "worker_session_exited" && e.payload.session_id === last.id)!;
    const stop = b.events.filter((e) => e.type === "work_item_needs_human").at(-1)!;
    expect(last.status).toBe(status);
    expect(exit.payload).toEqual({ session_id: last.id, status, wall_ms: last.wall_ms, model: "claude-sonnet-4-5", tokens_in: last.tokens_in, tokens_cache_write: 0, tokens_cache_read: 0, tokens_out: last.tokens_out, cost_usd: last.cost_usd });
    expect([last.exited_at, exit.seq < stop.seq]).toEqual([exit.created_at, true]);
    expect(recent(b.events as any).map((l) => l.line)).not.toContain("a session is running on verification");
  });

  it.each([
    // A person's stop ends the running attempt with `worker_session_paused`, never an exit: `store.pause_work_item`
    // after the pause it names the session in, `store.end_work_item` before the end.
    ["kraft-4e19, paused", "kraft-4e19", ["pause_requested", "worker_session_paused"]],
    ["kraft-7c40, paused, then queued on resume", "kraft-7c40", ["pause_requested", "worker_session_paused"]],
    ["kraft-b2e7, paused, then blocked on resume", "kraft-b2e7", ["pause_requested", "worker_session_paused"]],
    ["kraft-88ca, cancelled", "kraft-88ca", ["worker_session_paused", "work_item_cancelled", "work_item_abandoned"]],
  ])("%s: its stopped attempt is paused on the timeline as the store writes it, and Recent has it ended", (_name, bead, order) => {
    const { S } = server();
    const b = S.bundles[idOf(S, bead)];
    const last = b.sessions.filter((s) => s.hook_point === "verification.review.code_review").at(-1);
    const ends = b.events.filter((e) => e.payload.session_id === last.id && e.type !== "worker_session_started");
    expect([last.status, ends.map((e) => [e.type, e.payload]), last.exited_at]).toEqual(["paused", [["worker_session_paused", { session_id: last.id }]], ends[0]?.created_at]);
    // No writer sets a paused row's wall time: it is its stamps' span (`_common.session_wall_ms`).
    expect(last.wall_ms).toBe(Date.parse(last.exited_at) - Date.parse(last.started_at));
    // `toBe` passes NaN against NaN: a span of missing stamps would slip through without this.
    expect(last.wall_ms).toBeGreaterThan(0);
    expect(b.events.filter((e) => order.includes(e.type)).map((e) => [e.type, e.type === "pause_requested" ? e.payload.sessions : undefined])).toEqual(order.map((t) => [t, t === "pause_requested" ? [last.id] : undefined]));
    expect(recent(b.events as any).map((l) => l.line)).not.toContain("a session is running on verification");
  });

  it("kraft-a3f0's escalation turns count their attempts as `store.create_session` does", () => {
    const { S } = server();
    expect(S.bundles[idOf(S, "kraft-a3f0")].sessions.filter((s) => s.hook_point === "escalation").map((s) => s.attempt)).toEqual([1, 2]);
  });
});

describe("a created item", () => {
  it.each([
    ["filed paused", false, "paused", "paused"],
    ["started", true, "active", "running"],
  ])("shows on the board and opens, %s", async (_name, autostart, status, shown) => {
    const { call } = server();
    const before = (await call("GET", "/work-items")).body.items.length;
    const body = { title: "Cache embeddings by hash", description: "Bound it.", repo: "/Users/dev/code/kraft-api", chain_template: "docs_only", attachments: [{ kind: "spec", path: "/tmp/spec.md" }], autostart };
    const made = await call("POST", "/work-items", body);
    const id = made.body.id;
    expect(made).toEqual({ status: 201, body: autostart
      ? { id: expect.stringMatching(/^[0-9a-f]{32}$/), bead_id: null, status, chain_definition: expect.objectContaining({ template_id: "docs_only" }), materialized_chain: expect.any(String), current_node_id: "implementation" }
      : { id: expect.stringMatching(/^[0-9a-f]{32}$/), status } });
    const list = (await call("GET", "/work-items")).body.items;
    expect(list.length).toBe(before + 1);
    expect(list.at(-1)).toMatchObject({ id, title: "Cache embeddings by hash", repo: "/Users/dev/code/kraft-api", chain_template: "docs_only", status, display_status: shown, current_node_id: autostart ? "implementation" : null });
    const detail = (await call("GET", `/work-items/${id}`)).body;
    // The chain the composer's dry run showed: docs_only, less what the spec covers.
    const nodes = (ngDryRun(body).body as { nodes: { id: string }[] }).nodes.map((n) => n.id);
    expect(nodes).toEqual(["implementation", "work_brief", "merge_request"]);
    expect(detail).toMatchObject({
      id, title: "Cache embeddings by hash", description: "Bound it.", display_status: shown, worker_sessions: [],
      chain_definition: { template_id: "docs_only" }, effective_chain: { template_id: "docs_only" },
      worktree_path: null, head_sha: null, escalation_threads: [], stop: null, pending_gate: null,
      summary: { nodes_done: 0, nodes_total: 3, gates_passed: 0 }, budget_cap: { spent_usd: 0 },
      usage: { total: { cost_usd: 0, sessions: 0 }, by_node: [] },
    });
    expect([detail.chain_definition.nodes, detail.effective_chain.nodes, JSON.parse(detail.materialized_chain).chain.nodes].map((ns: any[]) => ns.map((n) => n.id))).toEqual([nodes, nodes, nodes]);
    const events = (await call("GET", `/work-items/${id}/events?after_seq=0`)).body;
    expect(events.map((e: any) => [e.type, e.payload])).toEqual([
      ["work_item_created", { title: "Cache embeddings by hash", repo: "/Users/dev/code/kraft-api", chain_template: "docs_only" }],
      ["work_item_attachments", { attachments: [{ kind: "spec", path: "/tmp/spec.md" }] }],
    ]);
    const docs = (await call("GET", `/work-items/${id}/documents`)).body.documents;
    expect(docs.map((d: any) => d.attachment_kind)).toEqual(["spec"]);
    // No worktree and no base commit until the walk first enters a node: the server's empty diff, and its 409 for a
    // comparison, which it answers before it looks for a worktree.
    expect(detail.worktree_exists).toBe(false);
    expect((await call("GET", `/work-items/${id}/diff`)).body).toMatchObject({ base_ref: null, files: [], landed: { files: [] } });
    expect(await call("GET", `/work-items/${id}/compare`)).toEqual({ status: 409, body: { detail: "this work item has no base commit to compare against" } });
  });

  it("is not made from a sample item, so an edit to one never reaches it", async () => {
    const { S, call } = server();
    const sample = S.bundles[idOf(S, "kraft-f5d3")];
    sample.item.title = "edited"; sample.item.budget_cap.spent_usd = 99; sample.sessions.length = 0;
    const id = (await call("POST", "/work-items", { title: "fresh" })).body.id;
    const detail = (await call("GET", `/work-items/${id}`)).body;
    expect([detail.title, detail.budget_cap.spent_usd, detail.chain_definition.nodes.length]).toEqual(["fresh", 0, 15]);
  });

  it("is filed in the empty scenario too", async () => {
    const { call } = server("empty");
    const made = await call("POST", "/work-items", { title: "first" });
    expect(made.status).toBe(201);
    expect((await call("GET", "/work-items")).body.items.map((i: any) => i.title)).toEqual(["first"]);
  });

  it("takes the next id each time", async () => {
    const { call } = server();
    const a = (await call("POST", "/work-items", { title: "one" })).body.id;
    const b = (await call("POST", "/work-items", { title: "two" })).body.id;
    expect(a).not.toBe(b);
  });
});

describe("onEvent", () => {
  it("hears every event an action appends, in order", async () => {
    const { S, call, told } = server();
    const id = idOf(S, "kraft-91bc");
    const had = S.bundles[id].events.length;
    await call("POST", `/work-items/${id}/pause`);
    expect(told).toEqual(S.bundles[id].events.slice(had));
    expect(told.map((e) => e.type)).toEqual(["pause_requested", "worker_session_paused"]);
  });

  it("the list's cursor is the newest seq, so a socket opened after an action does not replay it", async () => {
    const { S, call, told } = server();
    const before = (await call("GET", "/work-items")).body.cursor;
    await call("POST", `/work-items/${idOf(S, "kraft-91bc")}/pause`);
    const after = (await call("GET", "/work-items")).body.cursor;
    expect([after, after - before]).toEqual([told.at(-1)!.seq, told.length]);
  });
});

describe("the chain draft's ops", () => {
  const dig = (o: unknown, field: string) => field.split(".").reduce<any>((a, k) => a?.[k], o);
  const at = (chain: { nodes: { id: string }[] }, path: string) => (path ? chain.nodes.find((n) => n.id === path) : chain) as Record<string, unknown>;
  const set = (path: string, field: string, value: unknown) => ({ op: "set_field", path, field, value });
  it.each<[string, Record<string, unknown>[], string, string, string[], unknown, unknown]>([
    ["sets a field, its Config row now the chain's", [set("security_approval", "reject_to", "verification")], "security_approval", "reject_to",
      ["id", "kind", "message", "reject_to"], "verification", { value: "verification", source: "chain" }],
    ["clears one the chain set, its row back to the default", [set("security_approval", "message", null)], "security_approval", "message",
      ["id", "kind"], undefined, { value: null, source: "default" }],
    ["sets a dotted field nested", [set("spec", "policy.time_cap_minutes", 30)], "spec", "policy.time_cap_minutes",
      ["id", "kind", "steps", "policy"], 30, { value: 30, source: "chain" }],
    ["unsets a dotted field and the mapping it empties", [set("spec", "policy.time_cap_minutes", 30), set("spec", "policy.time_cap_minutes", "")], "spec", "policy.time_cap_minutes",
      ["id", "kind", "steps"], undefined, { value: null, source: "default" }],
    ["sets the chain's own field at the empty path", [set("", "description", "Ships it.")], "", "description",
      ["id", "nodes", "description"], "Ships it.", undefined],
  ])("set_field %s", async (_, ops, path, field, keys, read, source) => {
    const serve = mockServer(buildScenario("default"));
    const r = JSON.parse(((await serve({ method: "POST", url: "http://demo.test/api/drafts/chains/default/ops", json: () => ({ ops }) })) as Answer).body!).result;
    const authored = at(r.model["chains/default.yaml"], path);
    expect([Object.keys(authored), dig(authored, field), dig(at(r.resolved.chain, path), field), r.sources[path]?.[field], r.changes.filter((c: { path: string }) => c.path === path).length])
      .toEqual([keys, read, read, source, 1]);
  });
});

describe("a chain draft, kept for the server's life", () => {
  const BASE = "http://demo.test/api/drafts/chains/default";
  const kept = () => {
    const serve = mockServer(buildScenario("default"));
    const call = async (method: string, path = "", body: unknown = null) => {
      const a = (await serve({ method, url: BASE + path, json: () => body })) as Answer;
      return { status: a.status, body: a.body ? JSON.parse(a.body) : null };
    };
    const set = (field: string, value: unknown) => call("POST", "/ops", { ops: [{ op: "set_field", path: "security_approval", field, value }] });
    const gate = async () => (await call("GET")).body.result.model["chains/default.yaml"].nodes.find((n: { id: string }) => n.id === "security_approval");
    return { call, set, gate };
  };

  it.each<[string, (k: ReturnType<typeof kept>) => Promise<unknown>, Record<string, unknown>]>([
    ["two edits in two requests both stick", async ({ set }) => { await set("reject_to", "work_brief"); await set("timeout", "2d"); }, { reject_to: "work_brief", timeout: "2d" }],
    ["a preview changes nothing", async ({ call }) => call("POST", "/ops?preview=1", { ops: [{ op: "set_field", path: "security_approval", field: "reject_to", value: "spec" }] }), {}],
    ["undo puts back the draft before the last edit", async ({ set, call }) => { await set("reject_to", "work_brief"); await set("timeout", "2d"); await call("POST", "/undo"); }, { reject_to: "work_brief" }],
    ["a discard drops the draft, so the next read is the sample", async ({ set, call }) => { await set("reject_to", "work_brief"); await call("DELETE"); }, {}],
  ])("%s", async (_, act, fields) => {
    const k = kept();
    await act(k);
    expect(await k.gate()).toEqual({ id: "security_approval", kind: "gate", message: "Approve the security scan.", ...fields });
  });

  it("publishes the draft: no changes left, nothing to undo", async () => {
    const { set, call } = kept();
    await set("reject_to", "work_brief");
    await call("POST", "/publish");
    const after = (await call("GET")).body;
    expect([after.draft, after.result.changes, (await call("POST", "/undo")).status]).toEqual([false, [], 409]);
  });
});
