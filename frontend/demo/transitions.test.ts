import { describe, expect, it } from "vitest";
import { buildScenario, type Scenario } from "../e2e/contract/mocks/fixtures";
import { mockServer, type Answer } from "../e2e/contract/mocks/mockApi";
import { NG_NOW } from "../e2e/contract/mocks/ngItems";
import { displayStatus, transitions, type Transitions } from "../e2e/contract/mocks/transitions";

/** The mock's item transitions (transitions.ts): what each door changes, the events it appends, the words it refuses in. */

const AT = "2026-09-13T10:12:00.000Z";
/** A fresh scenario and its transitions, the clock at AT. */
const fresh = (mustFix: string[] = []) => {
  const S = buildScenario("default");
  return { S, T: transitions(S, { now: () => AT, mustFix: () => mustFix }) };
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
});

type Row = [string, string, (T: Transitions, id: string) => { status: number }, Record<string, unknown>, string[]];

describe("transitions", () => {
  it.each<Row>([
    ["approve clears the gate and runs on", "needs-gate", (T, id) => T.approve(id, "final_review"), { status: "active", display_status: "running", pending_gate: null, stop: null }, ["gate_approved", "node_completed"]],
    ["reject goes back to the gate's reject_to", "needs-gate", (T, id) => T.reject(id, "final_review", "too vague"), { status: "active", display_status: "running", pending_gate: null, current_node_id: "implementation" }, ["gate_rejected"]],
    ["pause stops the node's running session", "running", (T, id) => T.pause(id), { status: "paused", display_status: "paused" }, ["pause_requested", "worker_session_paused"]],
    ["resume runs a paused item again", "paused", (T, id) => T.resume(id), { status: "active", display_status: "running" }, ["work_item_resumed"]],
    ["retry runs a stopped item on its node", "failed", (T, id) => T.retry(id, "use the new token"), { status: "active", display_status: "running", stop: null, current_node_id: "merge_request" }, ["work_item_retried"]],
    ["skip passes the node it stands on", "failed", (T, id) => T.skip(id, "merge_request", "opened by hand"), { status: "active", display_status: "running", stop: null }, ["node_skipped"]],
    ["skip passes one task by path", "failed", (T, id) => T.skip(id, "merge_request.open.open_draft"), { status: "active", display_status: "running" }, ["scope_skipped"]],
    ["cancel ends it abandoned, its session stopped", "running", (T, id) => T.end(id, "cancel", "superseded"), { status: "abandoned", display_status: "cancelled" }, ["worker_session_paused", "work_item_cancelled", "work_item_abandoned"]],
    ["complete ends it completed", "running", (T, id) => T.end(id, "complete", "landed elsewhere"), { status: "completed", display_status: "done" }, ["worker_session_paused", "work_item_manually_completed", "work_item_completed"]],
    ["abandon ends a stopped item", "failed", (T, id) => T.abandon(id), { status: "abandoned", display_status: "cancelled" }, ["work_item_abandoned"]],
    ["archive files an ended item away", "kraft-3a90", (T, id) => T.archive(id), { status: "completed", display_status: "archived", archived_by: "you", archived_at: AT }, ["work_item_archived"]],
    ["restore puts it back under Done", "kraft-9a02", (T, id) => T.restore(id), { status: "completed", display_status: "done", archived_at: null }, ["work_item_restored"]],
    ["raise budget lifts the cap and retries", "kraft-58e2", (T, id) => T.raiseBudget(id, 8), { status: "active", display_status: "running", stop: null }, ["budget_raised", "work_item_retried"]],
    ["escalate starts a turn in a new thread", "failed", (T, id) => T.escalate(id, "look at the token", true), { status: "needs_human", display_status: "escalated" }, ["escalation_message"]],
    ["a title edit", "running", (T, id) => T.patch(id, { title: "Cache embeddings by hash" }), { title: "Cache embeddings by hash", display_status: "running" }, ["work_item_title_edited"]],
    ["a description edit", "running", (T, id) => T.patch(id, { description: "Bound the cache." }), { description: "Bound the cache." }, ["work_item_description_edited"]],
  ])("%s", (_name, at, act, after, types) => {
    const { S, T } = fresh();
    const id = idOf(S, at);
    const b = S.bundles[id];
    const had = b.events.length;
    const from = T.cursor();
    expect(act(T, id).status).toBeLessThan(300);
    expect(b.item).toMatchObject({ ...after, updated_at: AT });
    const added = b.events.slice(had);
    expect(added.map((e) => e.type)).toEqual(types);
    // Each with the next seq, in order, at the clock's instant.
    expect(added.map((e) => [e.seq, e.work_item_id, e.created_at])).toEqual(types.map((_, k) => [from + k + 1, id, AT]));
    expect(T.cursor()).toBe(from + types.length);
  });

  it.each([
    ["approve", (T: Transitions, id: string) => T.approve(id, "final_review"), { type: "gate_approved", payload: { gate: "final_review", by: "human" }, node_id: "final_review" }],
    ["reject", (T: Transitions, id: string) => T.reject(id, "final_review", "too vague"), { type: "gate_rejected", payload: { gate: "final_review", note: "too vague", node: "implementation", by: "human", verdict: "reject" } }],
  ])("%s's event says what was decided", (_name, act, event) => {
    const { S, T } = fresh();
    const id = S.ng["needs-gate"];
    act(T, id);
    expect(S.bundles[id].events.filter((e) => e.type === event.type).at(-1)).toMatchObject(event);
  });

  it.each([
    ["resume with a steer queues a new attempt carrying it", (T: Transitions, id: string) => T.resume(id, "look at the race"), "work_item_resumed", { steer: "look at the race" }, 1],
    ["escalate runs a turn of its own", (T: Transitions, id: string) => T.escalate(id, "look at the race"), "escalation_message", { message: "look at the race", auto: false, thread: 1, turn: 1 }, 1],
  ])("%s", (_name, act, type, payload, more) => {
    const { S, T } = fresh();
    const id = S.ng.paused;
    const b = S.bundles[id];
    const n = b.sessions.length;
    act(T, id);
    expect(b.events.at(-1)).toMatchObject({ type, payload });
    expect(b.sessions.length).toBe(n + more);
    expect(b.sessions.at(-1)).toMatchObject({ node_id: b.item.current_node_id, status: expect.stringMatching(/^(pending|running)$/), exited_at: null });
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

describe("refusals", () => {
  const cases: [string, string, (T: Transitions, id: string) => { status: number; body: any }, number, string, string[]?][] = [
    ["approve, no gate pending", "running", (T, id) => T.approve(id, "final_review"), 409, "gate 'final_review' is not pending"],
    ["approve, a gate the chain does not have", "needs-gate", (T, id) => T.approve(id, "nope"), 404, "unknown gate 'nope'"],
    ["approve, an open must-fix", "needs-gate", (T, id) => T.approve(id, "final_review"), 409, "must-fix review threads are not resolved: th-must, th-two", ["th-must", "th-two"]],
    ["approve, an ended item", "done", (T, id) => T.approve(id, "final_review"), 409, "work item is completed; its chain does not run again"],
    ["reject forward, past the gate", "needs-gate", (T, id) => T.reject(id, "final_review", "no", "release"), 400, "cannot reject to 'release': not a node of this chain before 'final_review'"],
    ["pause, a paused item", "paused", (T, id) => T.pause(id), 409, "work item is paused, not running"],
    ["resume, a running item", "running", (T, id) => T.resume(id), 409, "work item is active: it is running, so pause it first"],
    ["resume, at a gate", "needs-gate", (T, id) => T.resume(id), 409, "work item is waiting at its final_review gate, not paused: approve or reject it (kraft item approve ID, or kraft item reject ID --note ...)"],
    ["resume, a stopped item", "failed", (T, id) => T.resume(id), 409, "work item is needs_human, not paused: it stopped, so retry it instead (kraft item retry ID, which takes --steer too)"],
    ["retry, a paused item", "paused", (T, id) => T.retry(id), 409, "work item is paused, not stopped: resume it instead, or skip what it would run"],
    ["retry, a running item", "running", (T, id) => T.retry(id), 409, "work item is not stopped"],
    ["retry, never started", "kraft-f5d3", (T, id) => T.retry(id), 409, "work item has no current node to retry"],
    ["skip, an ended item", "done", (T, id) => T.skip(id, "release"), 409, "work item is completed, cannot skip"],
    ["skip, another node", "failed", (T, id) => T.skip(id, "release"), 409, "path: 'release' is not the node the item stands on ('merge_request') or its pending gate"],
    ["cancel, no reason", "running", (T, id) => T.end(id, "cancel", "  "), 422, "reason: a terminal action needs a reason"],
    ["cancel, an ended item", "done", (T, id) => T.end(id, "cancel", "dup"), 409, "work item is already completed"],
    ["abandon, a running item", "running", (T, id) => T.abandon(id), 409, "work item is active; pause it before abandoning"],
    ["archive, a running item", "running", (T, id) => T.archive(id), 409, "only a completed or abandoned item can be archived"],
    ["restore, an item not archived", "done", (T, id) => T.restore(id), 409, "work item is not archived"],
    ["raise budget, a running item", "running", (T, id) => T.raiseBudget(id, 9), 409, "work item is not stopped"],
    ["raise budget, not a spend stop", "failed", (T, id) => T.raiseBudget(id, 9), 409, "work item was not stopped by a spend cap"],
    ["raise budget, under what it spent", "kraft-58e2", (T, id) => T.raiseBudget(id, 2), 422, "the item has already spent $2.41; a cap of $2 would stop it again at once: raise it above that, or to none"],
    ["escalate, a running item", "running", (T, id) => T.escalate(id, "help"), 409, "work item is not needs_human or paused"],
    ["escalate, never started", "kraft-f5d3", (T, id) => T.escalate(id, "help"), 409, "work item has not started"],
    ["escalate, a spend stop", "kraft-58e2", (T, id) => T.escalate(id, "help"), 409, "a spend cap stopped this item, and an escalation's agent would hit it too: raise it with `kraft item raise-budget ID --usd N` where that takes it (`kraft view show ID` names the cap), then escalate"],
    ["escalate, no message", "failed", (T, id) => T.escalate(id, " "), 400, "message is required"],
    ["escalate, a turn already running", "failed", (T, id) => (T.escalate(id, "one"), T.escalate(id, "two")), 409, "an escalation turn (SESSION) is already running"],
    ["patch, an empty title", "running", (T, id) => T.patch(id, { title: " " }), 422, "title cannot be empty"],
    ["patch, nothing to patch", "running", (T, id) => T.patch(id, {}), 422, "nothing to patch: send a title, description, chain, agent_overrides, node_overrides, policy, attachments, or budget_usd"],
    ["any door, an unknown item", "running", (T) => T.pause("f".repeat(32)), 404, "work item not found"],
  ];
  it.each(cases)("%s", (_name, at, act, status, detail, mustFix) => {
    const { S, T } = fresh(mustFix);
    const id = idOf(S, at);
    const b = S.bundles[id];
    const before = JSON.stringify(b.item);
    const had = b.events.length;
    const r = act(T, id);
    const sid = b.sessions.at(-1)?.id;
    expect([r.status, r.body.detail]).toEqual([status, detail.replaceAll("ID", id).replace("SESSION", sid)]);
    // A refusal changes nothing (but the first of two escalations).
    if (!detail.includes("SESSION")) expect([JSON.stringify(b.item), b.events.length]).toEqual([before, had]);
  });
});

/** The mock on the wire, as the demo and the contract suite put it there. */
const server = () => {
  const S = buildScenario("default");
  const serve = mockServer(S, { ngBoard: true, now: () => NG_NOW });
  const call = async (method: string, path: string, body?: unknown) => {
    const a = (await serve({ method, url: `http://x/api${path}`, json: () => body ?? null })) as Answer;
    return { status: a.status, body: a.body ? JSON.parse(a.body) : null };
  };
  return { S, call };
};

describe("the list and the detail", () => {
  it.each([
    ["pause", "kraft-91bc", "POST", "pause", {}, "paused", false],
    ["approve at the gate", "kraft-cb59", "POST", "gates/final_review/approve", {}, "running", false],
    ["reject at the gate", "kraft-cb59", "POST", "gates/final_review/reject", { note: "no" }, "running", false, "implementation"],
    ["a review's request changes at the gate", "kraft-cb59", "POST", "gates/final_review/review", { outcome: "request_changes" }, "running", false, "implementation"],
    ["resume", "kraft-4e19", "POST", "resume", { steer: "go on" }, "running", false],
    ["retry", "kraft-b108", "POST", "retry", {}, "running", false],
    ["cancel", "kraft-91bc", "POST", "cancel", { reason: "dup" }, "cancelled", false],
    ["escalate", "kraft-b108", "POST", "escalate", { message: "help" }, "escalated", false],
    ["archive", "kraft-3a90", "POST", "archive", {}, "archived", true],
    ["a bulk pause", "kraft-91bc", "POST", "../bulk", { action: "pause", ids: ["ID"] }, "paused", false],
  ] as [string, string, string, string, object, string, boolean, string?][])("agree after %s", async (_name, bead, method, door, body, shown, archived, node) => {
    const { S, call } = server();
    const id = idOf(S, bead);
    const at = S.bundles[id].item.current_node_id;
    // The board has read its list before the action, as it does on boot.
    expect((await call("GET", "/work-items")).body.items.some((i: any) => i.id === id)).toBe(true);
    const sent = JSON.parse(JSON.stringify(body).replace("ID", id));
    const r = await call(method, door.startsWith("../") ? `/work-items/${door.slice(3)}` : `/work-items/${id}/${door}`, sent);
    expect(r.status, JSON.stringify(r.body)).toBeLessThan(300);
    const list = await call("GET", `/work-items${archived ? "?archived=true" : ""}`);
    const row = list.body.items.find((i: any) => i.id === id);
    const detail = (await call("GET", `/work-items/${id}`)).body;
    expect([row.display_status, row.status, row.updated_at, row.current_node_id]).toEqual([shown, detail.status, NG_NOW, node ?? at]);
    expect([detail.display_status, detail.current_node_id]).toEqual([row.display_status, row.current_node_id]);
  });

  it("answers a bulk door's refusal in that door's words", async () => {
    const { S, call } = server();
    const [run, paused] = [idOf(S, "kraft-91bc"), idOf(S, "kraft-4e19")];
    const r = await call("POST", "/work-items/bulk", { action: "pause", ids: [run, paused, "nope"] });
    expect(r.body.results).toEqual([
      { id: run, ok: true, status: "paused" },
      { id: paused, ok: false, status: "paused", error: "work item is paused, not running" },
      { id: "nope", ok: false, error: "not found" },
    ]);
  });
});

describe("a created item", () => {
  it.each([
    ["filed paused", false, "paused", "paused"],
    ["started", true, "active", "running"],
  ])("shows on the board and opens, %s", async (_name, autostart, status, shown) => {
    const { call } = server();
    const before = (await call("GET", "/work-items")).body.items.length;
    const made = await call("POST", "/work-items", { title: "Cache embeddings by hash", description: "Bound it.", repo: "/Users/dev/code/kraft-api", chain_template: "docs_only", attachments: [{ kind: "spec", path: "/tmp/spec.md" }], autostart });
    expect(made).toEqual({ status: 201, body: { id: expect.stringMatching(/^[0-9a-f]{32}$/), status, bead_id: null } });
    const id = made.body.id;
    const list = (await call("GET", "/work-items")).body.items;
    expect(list.length).toBe(before + 1);
    expect(list.at(-1)).toMatchObject({ id, title: "Cache embeddings by hash", repo: "/Users/dev/code/kraft-api", chain_template: "docs_only", status, display_status: shown, current_node_id: null });
    const detail = (await call("GET", `/work-items/${id}`)).body;
    expect(detail).toMatchObject({ id, title: "Cache embeddings by hash", description: "Bound it.", display_status: shown });
    expect(detail.worker_sessions.length).toBeGreaterThan(0);
    expect(detail.worker_sessions.every((s: any) => s.work_item_id === id)).toBe(true);
    const events = (await call("GET", `/work-items/${id}/events?after_seq=0`)).body;
    expect(events.map((e: any) => [e.type, e.payload.title])).toEqual([["work_item_created", "Cache embeddings by hash"]]);
    const docs = (await call("GET", `/work-items/${id}/documents`)).body.documents;
    expect(docs.map((d: any) => d.attachment_kind).filter(Boolean)).toEqual(["spec"]);
    const log = await call("GET", `/work-items/${id}/sessions/${detail.worker_sessions[0].id}/log?format=jsonl`);
    expect(log.body.lines.length).toBeGreaterThan(0);
    expect((await call("GET", `/work-items/${id}/diff`)).body.files.length).toBeGreaterThan(0);
  });

  it("takes the next id each time", async () => {
    const { call } = server();
    const a = (await call("POST", "/work-items", { title: "one" })).body.id;
    const b = (await call("POST", "/work-items", { title: "two" })).body.id;
    expect(a).not.toBe(b);
  });
});
