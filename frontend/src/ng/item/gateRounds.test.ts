import { describe, expect, it } from "vitest";
import type { GateRound } from "../../types";
import { byWord, gateRoundList, gateState, isPastRound, newestOutcome, outcomeWord, roundShown } from "./gateRounds";
import { detail, SLICED } from "./testkit";
import type { ItemDetail } from "./useItem";

const round = (n: number, over: Partial<GateRound> = {}): GateRound => ({
  n, requested_at: "t", ended_at: "t", outcome: "approved", by: "assistant", note: null, sent_to: null, reopened: false, reviewer: null, ...over,
});
const rejected = round(1, { outcome: "rejected", note: "redo task 3", sent_to: "plan" });
const approved = round(2);
const withRounds = (rounds: GateRound[], over: Partial<ItemDetail> = {}) => detail({ gate_rounds: { plan_approval: rounds }, ...over });

describe("byWord", () => {
  it.each([
    ["human", "you"],
    ["assistant", "assistant"],
    ["agent", "auto"],
    ["kraft", "auto"],
    [null, null],
  ] as const)("%s says %s", (by, word) => {
    expect(byWord(by)).toBe(word);
  });
});

describe("outcomeWord", () => {
  it.each([
    ["approved", "approved"],
    ["rejected", "changes requested"],
    ["fixed", "fixed"],
    ["skipped", "skipped"],
    ["closed", "closed"],
    ["pending", "waiting"],
  ] as const)("%s", (outcome, word) => {
    expect(outcomeWord(round(1, { outcome }))).toBe(word);
  });
});

describe("gateState", () => {
  it.each<[string, ItemDetail, { state: string; words: string }]>([
    ["the item's pending gate", withRounds([rejected, round(2, { outcome: "pending", ended_at: null, by: null })], { pending_gate: "plan_approval" }), { state: "current", words: "waiting for you" }],
    ["newest approved by the assistant", withRounds([rejected, approved]), { state: "done", words: "passed · assistant" }],
    ["newest approved by you", withRounds([round(1, { by: "human" })]), { state: "done", words: "passed · you" }],
    ["newest approved but reopened", withRounds([round(1, { reopened: true })]), { state: "todo", words: "not reached" }],
    ["newest rejected", withRounds([approved, round(2, { outcome: "rejected" })]), { state: "todo", words: "not reached" }],
    ["newest closed", withRounds([round(1, { outcome: "closed", by: null })]), { state: "todo", words: "not reached" }],
    ["no rounds, the run not there yet", detail({ current_node_id: "plan" }), { state: "todo", words: "not reached" }],
    ["skipped", withRounds([round(1, { outcome: "skipped", by: null })]), { state: "done", words: "skipped" }],
    ["another gate's skip", detail({ current_node_id: "plan", gate_rounds: { final_review: [round(1, { outcome: "skipped", by: null })] } }), { state: "todo", words: "not reached" }],
    // A run inside a later group stands at `slices.<node>`: the gate before it is behind it.
    ["behind a run inside a group", detail({ chain_definition: { template_id: "d", nodes: SLICED }, current_node_id: "slices.implementation" }), { state: "done", words: "passed" }],
  ])("%s", (_name, item, expected) => {
    expect(gateState(item, "plan_approval")).toEqual(expected);
  });
});

describe("gateRoundList", () => {
  it("is nothing for one round", () => {
    expect(gateRoundList(withRounds([approved]), "plan_approval")).toBeUndefined();
  });

  it.each([
    ["the newest by default", undefined, 2],
    ["the picked round", 1, 1],
    ["the newest for a round it hasn't got", 5, 2],
  ])("shows %s", (_name, picked, selected) => {
    expect(gateRoundList(withRounds([rejected, approved]), "plan_approval", picked)).toEqual({
      rows: [
        { n: 1, tone: "bad", outcome: "changes requested · assistant" },
        { n: 2, tone: "ok", outcome: "approved · assistant" },
      ],
      selected,
      latest: 2,
    });
  });

  it("dots a waiting round blue, and names nobody for it", () => {
    const list = gateRoundList(withRounds([rejected, round(2, { outcome: "pending", by: null, ended_at: null })]), "plan_approval");
    expect(list?.rows[1]).toEqual({ n: 2, tone: "run", outcome: "waiting" });
  });
});

describe("roundShown", () => {
  it.each([
    ["none picked", undefined, 2],
    ["a round picked", 1, 1],
    // A link with a stale ?pass= shows the newest round, as a node's passes do.
    ["a stale pick", 5, 2],
  ])("%s", (_name, picked, n) => {
    expect(roundShown(withRounds([rejected, approved]), "plan_approval", picked)?.n).toBe(n);
  });

  it("is nothing for a gate with no round", () => {
    expect(roundShown(detail(), "plan_approval")).toBeUndefined();
  });
});

describe("isPastRound", () => {
  it.each([
    ["the first of two", 1, true],
    ["the newest of two", 2, false],
    ["none picked", undefined, false],
    ["a stale pick, which shows the newest", 5, false],
  ])("%s", (_name, picked, past) => {
    expect(isPastRound(withRounds([rejected, approved]), "plan_approval", picked)).toBe(past);
  });
});

// The pane's subtitle and the diamond's badge say this for a gate that is neither waiting nor passed.
describe("newestOutcome", () => {
  it.each<[string, GateRound[], Partial<ItemDetail>, string | undefined]>([
    ["sent work back", [round(1, { by: "human" }), round(2, { outcome: "rejected", by: "human" })], {}, "changes requested · you"],
    ["closed, naming no one", [round(1, { outcome: "closed", by: null })], {}, "closed"],
    ["approved then reopened", [round(1, { reopened: true })], {}, "approved · assistant"],
    ["passed: its state says that", [round(1)], {}, undefined],
    ["waiting", [round(1, { outcome: "rejected", by: "human" })], { pending_gate: "plan_approval" }, undefined],
    ["no round", [], {}, undefined],
  ])("%s", (_name, rounds, over, words) => {
    expect(newestOutcome(withRounds(rounds, over), "plan_approval")).toBe(words);
  });
});
