import { describe, expect, it } from "vitest";
import { deriveState } from "./deriveState";
import type { KraftEvent, WorkItem, WorkerSession } from "./types";

const BASE: WorkItem = {
  id: "wi_1",
  title: "t",
  repo: "/repo-a",
  status: "active",
  chain_template: "default",
  chain_definition: { template_id: "default", nodes: [] },
  current_node_id: "verify",
  bead_id: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("deriveState", () => {
  it.each<[string, Partial<WorkItem>, string, boolean]>([
    ["active → running", {}, "running", false],
    ["completed → done", { status: "completed" }, "done", false],
    ["abandoned → abandoned", { status: "abandoned" }, "abandoned", false],
    ["rate_limited → rate_limited", { status: "rate_limited" }, "rate_limited", false],
    ["waiting (ci_wait) → waiting", { status: "waiting" }, "waiting", false],
    ["paused with no current node → not_started", { status: "paused", current_node_id: null }, "not_started", false],
    ["mid-chain pause → paused", { status: "paused" }, "paused", true],
    ["pending gate → gate", { status: "needs_human", pending_gate: "human_review" }, "gate", true],
    ["capped-out stop → capped", { status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } }, "capped", true],
    ["budget stop → budget", { status: "needs_human", budget: { scope: "work_item", spent_usd: 5, cap_usd: 5 } }, "budget", true],
    ["needs_context question → question", { status: "needs_human", needs_context_question: "which backoff?" }, "question", true],
    // Retry, not a broken gate
    ["needs_human with no reason field → capped", { status: "needs_human" }, "capped", true],
  ])("maps %s", (_, over, state, needsYou) => {
    expect(deriveState({ ...BASE, ...over })).toEqual({ state, needsYou });
  });

  it("prefers pending_gate over a stale cappedOut/budget/question left on the item", () => {
    expect(
      deriveState({
        ...BASE,
        status: "needs_human",
        pending_gate: "human_review",
        cappedOut: { cycles: 3, attempts: 3 },
      }),
    ).toEqual({ state: "gate", needsYou: true });
  });
});

const escSession = (over: Partial<WorkerSession> = {}): WorkerSession =>
  ({
    id: "e1",
    work_item_id: "wi_1",
    node_id: "verify",
    hook_point: "escalation",
    status: "running",
    attempt: 1,
    round: 0,
    created_at: "2026-01-01T00:05:00Z",
    started_at: "2026-01-01T00:05:00Z",
    exited_at: null,
    tokens_in: null,
    tokens_out: null,
    cost_usd: null,
    wall_ms: null,
    model: null,
    head_sha: null,
    ...over,
  }) as WorkerSession;

const NEEDS_HUMAN_EVENT: KraftEvent = {
  seq: 1,
  work_item_id: "wi_1",
  type: "work_item_needs_human",
  payload: { reason: "loop capped" },
  created_at: "2026-01-01T00:00:00Z",
};

/** The `escalation_message` event `escalate.dispatch` appends for a turn —
 *  the id-based tie deriveState now scopes an episode by, in place of a
 *  timestamp compare (Kraft-bffrk). */
const escMessage = (over: Partial<KraftEvent> = {}): KraftEvent => ({
  seq: 2,
  work_item_id: "wi_1",
  type: "escalation_message",
  payload: { session_id: "e1", message: "go" },
  created_at: "2026-01-01T00:05:00Z",
  ...over,
});

describe("deriveState — escalation and archived", () => {
  it("maps a running escalation turn to escalating, not needs-you", () => {
    const item = { ...BASE, status: "needs_human" as const, cappedOut: { cycles: 3, attempts: 3 } };
    expect(
      deriveState(item, [escSession()], [NEEDS_HUMAN_EVENT, escMessage()]),
    ).toEqual({
      state: "escalating",
      needsYou: false,
    });
  });

  it("maps a finished escalation turn to escalated, needs-you", () => {
    const item = { ...BASE, status: "needs_human" as const, cappedOut: { cycles: 3, attempts: 3 } };
    const done = escSession({ status: "done_with_concerns", exited_at: "2026-01-01T00:06:00Z" });
    expect(deriveState(item, [done], [NEEDS_HUMAN_EVENT, escMessage()])).toEqual({
      state: "escalated",
      needsYou: true,
    });
  });

  it("ignores an escalation turn from a prior, already-resolved stop", () => {
    const item = { ...BASE, status: "needs_human" as const, cappedOut: { cycles: 3, attempts: 3 } };
    // The stale turn's own escalation_message predates the current episode's
    // boundary event (seq 1) — its session_id never lands in this episode's
    // set, however its created_at compares against the boundary.
    const stale = escSession({ status: "done", created_at: "2025-12-31T00:00:00Z" });
    const staleMessage = escMessage({ seq: 0, created_at: "2025-12-31T00:00:00Z" });
    expect(deriveState(item, [stale], [staleMessage, NEEDS_HUMAN_EVENT])).toEqual({
      state: "capped",
      needsYou: true,
    });
  });

  it("ignores an escalation turn superseded by a later gate stop (no new work_item_needs_human event)", () => {
    // escalate -> apply as steer & retry -> node reaches a gate: gate stops
    // emit `gate_requested`, not another `work_item_needs_human`, so the
    // episode boundary has to bound on both, same as `board._stop_reason`.
    const item = { ...BASE, status: "needs_human" as const, pending_gate: "human_review" };
    const stale = escSession({ status: "done", created_at: "2026-01-01T00:03:00Z" });
    const staleMessage = escMessage({ seq: 2, created_at: "2026-01-01T00:03:00Z" });
    const gateRequested: KraftEvent = {
      seq: 3,
      work_item_id: "wi_1",
      type: "gate_requested",
      payload: { gate: "human_review" },
      created_at: "2026-01-01T00:04:00Z",
    };
    expect(
      deriveState(item, [stale], [NEEDS_HUMAN_EVENT, staleMessage, gateRequested]),
    ).toEqual({
      state: "gate",
      needsYou: true,
    });
  });

  it("scopes a turn to its episode by event seq, not a created_at compare that can tie or invert by milliseconds", () => {
    // The race Kraft-bffrk observed live: the session row's created_at lands
    // 1.8ms *before* the boundary event's created_at (two different clocks
    // in the same request), so a `>=` timestamp compare would drop this
    // turn from its own episode. The escalation_message event's seq — not
    // its created_at — is what actually orders after the boundary.
    const item = { ...BASE, status: "needs_human" as const, cappedOut: { cycles: 3, attempts: 3 } };
    const turn = escSession({ created_at: "2026-01-01T00:00:00.000Z" });
    const boundary: KraftEvent = {
      ...NEEDS_HUMAN_EVENT,
      seq: 1,
      created_at: "2026-01-01T00:00:00.0018Z",
    };
    const message = escMessage({ seq: 2, created_at: "2026-01-01T00:00:00.000Z" });
    expect(deriveState(item, [turn], [boundary, message])).toEqual({
      state: "escalating",
      needsYou: false,
    });
  });

  it("maps an archived item to archived, not needs-you, regardless of status", () => {
    expect(
      deriveState({ ...BASE, status: "completed", archived_at: "2026-01-02T00:00:00Z" }),
    ).toEqual({ state: "archived", needsYou: false });
  });
});
