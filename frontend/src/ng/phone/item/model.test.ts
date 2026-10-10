import { describe, expect, it } from "vitest";
import type { DisplayStatus, WorkItemStop } from "../../../types";
import type { EventType } from "../../../types/vocab.generated";
import { chainGraph } from "../../item/graph";
import { detail } from "../../item/testkit";
import { FORGE_LOGIN_HINT } from "../../item/cause";
import { cardOf, kebabOf, nodeSub, pairOf } from "./model";

const stop = (kind: WorkItemStop["kind"], over: Partial<WorkItemStop> = {}): WorkItemStop => ({ kind, node: "verification", resume_at: null, reason: null, ...over });
const mk = (display_status: DisplayStatus, s: WorkItemStop | null = null, over = {}) => detail({ display_status, stop: s, ...over });
/** The item's own $10 cap, spent: the budget stop `/budget/raise` raises. */
const OWN_CAP = { budget_cap: { cap_usd: 10, source: "item", spent_usd: 10 } } as const;
const own = (over: Partial<WorkItemStop> = {}) => stop("budget", { scope: "work_item", ...over });
const POLICY_USD = { limit: { path: "", key: "budget_usd", value: 0.01, maximum: null } } as const;
const ids = (i: ReturnType<typeof detail>) => {
  const p = pairOf(i);
  return [p.secondary?.id ?? null, p.primary?.id ?? null];
};

describe("pairOf (C.5): one pair, by status and stop kind", () => {
  it.each([
    ["running", mk("running"), [ "pause", "steer"]],
    // R11b-01: /pause and /steer refuse a live escalation; a human's Retry outranks its turn.
    ["escalated", mk("escalated"), [null, "retry"]],
    ["paused mid-chain", mk("paused"), ["steer", "resume"]],
    ["not started", mk("paused", null, { current_node_id: null }), [null, "start"]],
    ["gate", mk("needs_you", stop("gate", { node: "plan_approval" })), ["reject", "review"]],
    // No Steer on a budget stop: a retry with one stops at the same cap again.
    ["budget, the item's own cap", mk("needs_you", own(), OWN_CAP), [null, "raise"]],
    ["budget, an item-wide policy budget_usd", mk("needs_you", stop("budget", POLICY_USD)), [null, "raise"]],
    ["budget, a cap the item cannot raise", mk("needs_you", stop("budget", { scope: "daily" }), OWN_CAP), [null, "retry"]],
    ["cap", mk("needs_you", stop("cap")), ["steer", "retry"]],
    ["cap with its limit", mk("needs_you", { ...stop("cap"), limit: { path: "", key: "time_cap_minutes", value: 480, maximum: null } }), ["steer", "raise"]],
    ["question", mk("needs_you", stop("question")), ["escalate", "answer"]],
    ["conflict", mk("needs_you", stop("conflict")), ["cancel", "conflicts"]],
    ["mr closed", mk("needs_you", stop("mr_closed")), ["cancel", "reopen-mr"]],
    ["failed", mk("failed", stop("failed")), ["escalate", "retry"]],
    ["rate limit", mk("waiting", stop("rate_limit")), ["pause", null]],
    ["waiting on CI", mk("waiting", stop("wait")), ["pause", null]],
    ["blocked", mk("blocked", null, { dependencies: [{ id: "a1", title: "Schema first", status: "active", met: false }] }), ["unblock", "pause"]],
    ["blocked, nothing left to wait for", mk("blocked", null, { dependencies: [] }), [null, "pause"]],
    ["done", mk("done"), [null, "board"]],
    ["cancelled", mk("cancelled"), [null, "board"]],
    ["archived", mk("archived"), [null, "restore"]],
  ] as const)("%s", (_name, item, want) => expect(ids(item)).toEqual(want));

  it("hides Steer when no agent task can read a note, and keeps the rest (#387)", () => {
    expect(ids(mk("paused", null, { steerable: false }))).toEqual([null, "resume"]);
    expect(ids(mk("running", null, { steerable: false }))).toEqual([null, "pause"]);
    expect(ids(mk("needs_you", own(), { steerable: false, ...OWN_CAP }))).toEqual([null, "raise"]);
    expect(ids(mk("paused", null, { steerable: true }))).toEqual(["steer", "resume"]);
    expect(ids(mk("paused", null, {}))).toEqual(["steer", "resume"]);
  });

  it("never offers Retry on a running item (Decisions §5)", () => {
    expect(ids(mk("running"))).not.toContain("retry");
  });
  it("has at most two buttons in every state", () => {
    for (const s of ["running", "waiting", "needs_you", "escalated", "failed", "paused", "done", "cancelled", "archived"] as const) {
      const p = pairOf(mk(s, s === "needs_you" ? stop("gate") : null));
      expect([p.secondary, p.primary].filter(Boolean).length).toBeLessThanOrEqual(2);
    }
  });
});

describe("kebabOf", () => {
  it("lists what the bar does not, cancel last and in the danger style", () => {
    const k = kebabOf(mk("running", null, { mr_ref: { number: 142, url: "https://x/142" } }));
    // No Escalate: /escalate refuses a running item.
    expect(k.map((a) => a.id)).toEqual(["settings", "open-mr", "duplicate", "complete", "cancel"]);
    expect(k.at(-1)).toMatchObject({ id: "cancel", danger: true });
    expect(k.find((a) => a.id === "open-mr")?.label).toBe("Open MR !142");
  });
  it("does not repeat a button the bar has, and archives only an ended item", () => {
    expect(kebabOf(mk("failed", stop("failed"))).map((a) => a.id)).not.toContain("escalate");
    expect(kebabOf(mk("running", null)).map((a) => a.id)).not.toContain("archive");
    expect(kebabOf(mk("cancelled")).map((a) => a.id)).toContain("archive");
  });
});

describe("cardOf: a gate's message (PH-14)", () => {
  const frozen = (message?: string) => JSON.stringify({ chain: { nodes: [{ id: "plan", kind: "exec" }, { id: "plan_approval", kind: "gate", ...(message && { message }) }] } });
  const at = (chain: string) => cardOf(mk("needs_you", stop("gate", { node: "plan_approval" }), { pending_gate: "plan_approval", materialized_chain: chain }));
  it("repeats the frozen chain's gate message on the card, and has none when the gate says nothing", () => {
    expect(at(frozen("Approve the plan."))?.text).toBe("Approve the plan.");
    expect(at(frozen())?.text).toBeUndefined();
  });
});

describe("cardOf: the words of the desktop's cards, from `stop` only", () => {
  it("has none for a plain running item and one for each stop", () => {
    expect(cardOf(mk("running"))).toBeNull();
    expect(cardOf(mk("needs_you", stop("gate", { node: "plan_approval" }), { pending_gate: "plan_approval" }))).toMatchObject({ title: "Waiting for your approval", where: "at plan_approval" });
    expect(cardOf(mk("needs_you", stop("budget", { reason: "The budget ran out." })))).toMatchObject({ tone: "bad", title: "The budget ran out", where: "at verification" });
    expect(cardOf(mk("needs_you", own(), OWN_CAP))?.text).toBe("Raising the budget resumes the item at once.");
    expect(cardOf(mk("needs_you", stop("budget", { scope: "daily" }), OWN_CAP))?.text).toMatch(/^The item can't raise this cap: the policy or the chain sets it\. Retry/);
    expect(cardOf(mk("needs_you", stop("cap", { reason: "Running time hit its 8h cap" })))?.text).toMatch(/Retry runs the node again/);
    expect(cardOf(mk("needs_you", stop("question", { task: "verification.review.code_review" }), { needs_context_question: "Allow it?" }))).toMatchObject({ title: "Needs you", text: "“Allow it?”", where: "asked by code_review · on verification" });
    expect(cardOf(mk("failed", stop("failed", { reason: "exit 1", task: "verification.review.code_review", attempt: 2 })))).toMatchObject({ tone: "bad", title: "Failed", text: "exit 1", where: "verification › review › code_review · attempt 2" });
    expect(cardOf(mk("waiting", stop("rate_limit", { facts: { harness: "claude", fallback_allowed: ["codex"] } })))?.text).toMatch(/claude hit its rate limit/);
    expect(cardOf(mk("needs_you", stop("conflict", { facts: { unresolved: ["a.py"], resolved: ["b.py"] } })))?.facts).toEqual([["unresolved", "a.py"], ["resolved", "b.py"]]);
    expect(cardOf(mk("blocked", null, { dependencies: [{ id: "a1", title: "Schema first", status: "needs_human", met: false }, { id: "b2", title: "Old cleanup", status: "completed", met: true }] })))
      .toMatchObject({ tone: "info", title: "Blocked", where: "waiting on 1 item", facts: [["needs human", "Schema first", "a1"], ["completed", "Old cleanup", "b2"]] });
    expect(cardOf(mk("blocked"))).toMatchObject({ where: "nothing left to wait for", text: "Kraft queues it within seconds.", facts: [] });
    expect(cardOf(mk("paused"))).toMatchObject({ title: "Paused", where: "at verification" });
    expect(cardOf(mk("paused", null, { current_node_id: null }))).toBeNull();
    expect(cardOf(mk("done", null, { mr_ref: { number: 7, url: "u" } }))).toMatchObject({ tone: "ok", where: "MR !7 merged" });
  });
  it("names the closer of a closed MR and the reason of a cancel from the events", () => {
    const ev = (type: EventType, payload: object) => ({ type, payload, created_at: "2026-09-13T08:00:00Z", node_id: null }) as never;
    expect(cardOf(mk("needs_you", stop("mr_closed", { facts: { ref: 142 } })), [ev("mr_closed", { by: "dana" })])?.where).toMatch(/closed by dana/);
    expect(cardOf(mk("cancelled"), [ev("work_item_cancelled", { reason: "wrong repo" })])?.facts).toContainEqual(["reason", "wrong repo"]);
  });
});

describe("the failed card's cause (R15b-01)", () => {
  const failedBy = (kind: WorkItemStop["kind"], cause?: string, over = {}) => mk("failed", stop(kind, { facts: cause ? { cause, command: "gh pr create" } : { command: "gh pr create" } }), over);
  const TESTS = { test_result: { passed: true, scopes: [{ command: "just test", scope: "**", passed: true, exit_code: 0, session_id: "s1" }] } };

  it.each([
    ["forge_auth on a failed stop", failedBy("failed", "forge_auth"), true],
    ["forge_auth on an infra stop", failedBy("infra", "forge_auth"), true],
    ["git", failedBy("infra", "git"), false],
    ["no cause", failedBy("failed"), false],
  ] as const)("%s: never prints the cause token, hints at the forge login only for a refused credential", (_name, item, hint) => {
    const card = cardOf(item);
    expect(card?.facts).toEqual([["command", "gh pr create"]]);
    expect(card?.hint).toBe(hint ? FORGE_LOGIN_HINT : undefined);
  });

  it("says what the run kept, as the desktop's card does: branch, files, passing tests", () => {
    expect(cardOf(failedBy("failed", "forge_auth", { branch: "kraft/x-w1", ...TESTS }), [], 2)?.facts).toEqual([["work kept", "branch kraft/x-w1 · 2 files · tests passing"], ["command", "gh pr create"]]);
    expect(cardOf(failedBy("failed", undefined, { branch: null }), [], 1)?.facts[0]).toEqual(["work kept", "1 file"]);
    expect(cardOf(failedBy("failed", undefined, { branch: null, test_result: { passed: false, scopes: [] } }), [], 0)?.facts).toEqual([["command", "gh pr create"]]);
  });

  it.each([
    ["an infra stop naming git: offered", failedBy("infra", "git"), true],
    ["an infra stop naming no cause: offered", failedBy("infra"), true],
    ["a refused credential (words on the card, no settings to open): not offered", failedBy("infra", "forge_auth"), false],
    ["a stranded claim (a retry is all it needs): not offered", failedBy("infra", "stranded"), false],
    ["a plain failed stop: not offered", failedBy("failed"), false],
    ["a running item: not offered", mk("running"), false],
  ] as const)("the ⋮ sheet's repo settings for %s", (_name, item, offers) => {
    expect(kebabOf(item).map((a) => a.id).includes("repo")).toBe(offers);
  });
});

describe("nodeSub: a chain row's words", () => {
  const graph = (i: ReturnType<typeof detail>) => chainGraph(i, [], Date.parse("2026-09-13T10:00:00Z")).nodes;
  // R15b-03: a gate skipped with `kraft item skip` was never approved, here as in its review path.
  it.each([
    ["approved by you reads approved", false, "approved"],
    ["skipped reads skipped", true, "skipped"],
  ])("a passed gate that was %s", (_name, skipped, text) => {
    const g = graph(mk("running", null, { current_node_id: "verification" })).find((n) => n.id === "plan_approval")!;
    expect(nodeSub(g, skipped).text).toBe(text);
  });
  it("reads each node's state", () => {
    const g = graph(mk("needs_you", stop("gate", { node: "plan_approval" }), { current_node_id: "plan_approval", pending_gate: "plan_approval" }));
    expect(nodeSub(g.find((n) => n.id === "plan")!).tone).toBe("muted");
    expect(nodeSub(g.find((n) => n.id === "plan_approval")!)).toEqual({ text: "waiting for you", tone: "warn" });
    expect(nodeSub(g.find((n) => n.id === "verification")!)).toEqual({ text: "not started", tone: "muted" });
    expect(nodeSub(graph(mk("running")).find((n) => n.id === "verification")!).text).toMatch(/^running/);
    expect(nodeSub(graph(mk("failed", stop("failed"))).find((n) => n.id === "verification")!)).toEqual({ text: "failed", tone: "bad" });
    expect(nodeSub(graph(mk("needs_you", stop("cap"))).find((n) => n.id === "verification")!)).toEqual({ text: "stopped at the cap", tone: "bad" });
    expect(nodeSub(graph(mk("paused")).find((n) => n.id === "verification")!)).toEqual({ text: "paused", tone: "warn" });
    // R11b-04: a waiting node says what it waits on, not "running".
    expect(nodeSub(graph(mk("waiting", stop("wait"))).find((n) => n.id === "verification")!).text).toMatch(/^waiting on CI/);
    expect(nodeSub(graph(mk("waiting", stop("rate_limit"))).find((n) => n.id === "verification")!).text).toMatch(/^waiting · rate limit/);
  });
});

describe("a budget stop's spent fact", () => {
  const spentOf = (s: WorkItemStop, budget_cap: object) => cardOf(mk("needs_you", s, { budget_cap }))?.facts;
  it("is against the policy budget_usd that stopped the item, not the item's $10 cap, to the cent like the reason", () => {
    const s = stop("budget", { reason: "budget_usd reached: $0.04 spent in the work item, cap $0.01.", ...POLICY_USD });
    expect(spentOf(s, { cap_usd: 10, source: "policy", spent_usd: 0.035 })).toEqual([["spent", "$0.04 of $0.01"]]);
  });
  it("is against the item's own cap when that stopped it", () => {
    expect(spentOf(own(), { cap_usd: 0.01, source: "item", spent_usd: 0.035 })).toEqual([["spent", "$0.04 of $0.01"]]);
  });
  it("names no cap for one the item cannot raise: the reason names it", () => {
    expect(spentOf(stop("budget", { scope: "daily" }), { cap_usd: 10, source: "policy", spent_usd: 2.5 })).toEqual([["spent", "$2.50"]]);
  });
});

describe("a question card (PH-12)", () => {
  const turn = (id: string, thread: number, created_at: string) => ({ id, node_id: "verification", hook_point: "verification.escalation", thread, created_at }) as never;
  const q = (task: string, sessions: unknown[] = []) => mk("needs_you", stop("question", { task }), { needs_context_question: "Allow it?", worker_sessions: sessions as never });
  it("has the speech bubble and names the thread and turn of an escalation's question", () => {
    const c = cardOf(q("verification.escalation", [turn("a", 1, "2026-09-13T09:00:00Z"), turn("b", 1, "2026-09-13T09:05:00Z")]));
    expect(c).toMatchObject({ icon: "message-square", where: "asked by escalation · thread 1, turn 2 · on verification" });
  });
  it("counts turns within the latest thread only", () => {
    const c = cardOf(q("verification.escalation", [turn("a", 1, "2026-09-13T09:00:00Z"), turn("b", 2, "2026-09-13T09:05:00Z")]));
    expect(c?.where).toBe("asked by escalation · thread 2, turn 1 · on verification");
  });
  it("leaves a task's own question as it was", () => {
    expect(cardOf(q("verification.review.code_review"))?.where).toBe("asked by code_review · on verification");
  });
});

describe("a cap stop's facts (PH-7)", () => {
  it("say the running time against its cap beside the spend", () => {
    const i = mk("needs_you", stop("cap"), { running_time: { running_s: 8 * 3600 + 120, cap_minutes: 480 }, budget_cap: { cap_usd: 5, source: "item", spent_usd: 3.72 } });
    expect(cardOf(i)?.facts).toEqual([["running", "8h 2m of 8h"], ["spent", "$3.72 of $5.00"]]);
  });
  it("leave the clock out when no time cap is set", () => {
    expect(cardOf(mk("needs_you", stop("cap"), { running_time: { running_s: 60, cap_minutes: null } }))?.facts).toEqual([]);
  });
});
