import { describe, expect, it } from "vitest";
import type { KraftEvent } from "../../types";
import type { EventType } from "../../types/vocab.generated";
import { reviewOutcome } from "./reviewOutcome";

let seq = 0;
const ev = (type: EventType, payload: Record<string, unknown>, gate = "plan_approval"): KraftEvent => ({ seq: ++seq, work_item_id: "w1", type, payload: { gate, ...payload }, node_id: gate, created_at: "t" });

describe("reviewOutcome", () => {
  it.each([
    ["an earlier round's reject does not hide this round's pass", [ev("gate_requested", {}), ev("gate_rejected", { by: "agent", note: "No tests." }), ev("gate_requested", {}), ev("gate_auto_review_skipped", { reason: "passed", note: "Looks right." })], { kind: "ready", note: "Looks right." }],
    ["an undecided skip carries its note", [ev("gate_requested", {}), ev("gate_auto_review_skipped", { reason: "undecided", note: "The reviewer changed files, so its verdict was ignored: a.py" })], { kind: "undecided", note: "The reviewer changed files, so its verdict was ignored: a.py" }],
    ["a budget skip is no outcome", [ev("gate_requested", {}), ev("gate_auto_review_skipped", { reason: "budget" })], null],
    ["nothing since the latest request is no outcome", [ev("gate_approved", { by: "agent" }), ev("gate_requested", {})], null],
    ["an agent's approve, with its note", [ev("gate_requested", {}), ev("gate_approved", { by: "agent", note: "Nothing blocking." })], { kind: "approve", note: "Nothing blocking." }],
    ["another gate's review is not this gate's", [ev("gate_requested", {}), ev("gate_auto_review_skipped", { reason: "passed" }, "final_review")], null],
  ])("%s", (_, events, want) => {
    expect(reviewOutcome(events, "plan_approval")).toEqual(want);
  });
});
