import { describe, expect, it } from "vitest";
import { deriveState } from "./deriveState";
import type { WorkItem } from "./types";

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
  it("maps an active item to running, not needs-you", () => {
    expect(deriveState(BASE)).toEqual({ state: "running", needsYou: false });
  });

  it("maps completed to done, not needs-you", () => {
    expect(deriveState({ ...BASE, status: "completed" })).toEqual({
      state: "done",
      needsYou: false,
    });
  });

  it("maps abandoned to abandoned, not needs-you", () => {
    expect(deriveState({ ...BASE, status: "abandoned" })).toEqual({
      state: "abandoned",
      needsYou: false,
    });
  });

  it("maps rate_limited to rate_limited, not needs-you", () => {
    expect(deriveState({ ...BASE, status: "rate_limited" })).toEqual({
      state: "rate_limited",
      needsYou: false,
    });
  });

  it("maps waiting (ci_wait) to waiting, not needs-you", () => {
    expect(deriveState({ ...BASE, status: "waiting" })).toEqual({
      state: "waiting",
      needsYou: false,
    });
  });

  it("maps a paused item with no current node to not_started, not needs-you", () => {
    expect(
      deriveState({ ...BASE, status: "paused", current_node_id: null }),
    ).toEqual({ state: "not_started", needsYou: false });
  });

  it("maps a mid-chain pause to paused, needs-you", () => {
    expect(deriveState({ ...BASE, status: "paused" })).toEqual({
      state: "paused",
      needsYou: true,
    });
  });

  it("maps a pending gate to gate, needs-you", () => {
    expect(
      deriveState({ ...BASE, status: "needs_human", pending_gate: "human_review" }),
    ).toEqual({ state: "gate", needsYou: true });
  });

  it("maps a capped-out stop to capped, needs-you", () => {
    expect(
      deriveState({
        ...BASE,
        status: "needs_human",
        cappedOut: { cycles: 3, attempts: 3 },
      }),
    ).toEqual({ state: "capped", needsYou: true });
  });

  it("maps a budget stop to budget, needs-you", () => {
    expect(
      deriveState({
        ...BASE,
        status: "needs_human",
        budget: { scope: "work_item", spent_usd: 5, cap_usd: 5 },
      }),
    ).toEqual({ state: "budget", needsYou: true });
  });

  it("maps a needs_context question to question, needs-you", () => {
    expect(
      deriveState({
        ...BASE,
        status: "needs_human",
        needs_context_question: "which backoff?",
      }),
    ).toEqual({ state: "question", needsYou: true });
  });

  it("falls back to gate for a needs_human stop with no reason field set", () => {
    expect(deriveState({ ...BASE, status: "needs_human" })).toEqual({
      state: "gate",
      needsYou: true,
    });
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
