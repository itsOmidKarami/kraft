import { describe, expect, it } from "vitest";
import type { ReviewResult, WorkItemDetail } from "../../src/core/api";
import { canSend, outcomeChoices, submitMessage, type Outcome } from "../../src/core/submit";
import { item } from "./fixtures/item";

describe("outcomeChoices", () => {
  it.each<{ id: string; detail: WorkItemDetail | undefined; want: ReturnType<typeof outcomeChoices> }>([
    { id: "no detail", detail: undefined, want: { refused: "Kraft has not loaded this item yet." } },
    { id: "done", detail: item({ display_status: "done" }), want: { refused: "This item has ended; its review is closed." } },
    { id: "a gate", detail: item({ pending_gate: "spec", display_status: "needs_you" }), want: ["request_changes", "comment", "approve"] },
    { id: "paused, no gate", detail: item({ status: "paused", display_status: "paused" }), want: ["request_changes"] },
    { id: "running, no gate", detail: item(), want: ["request_changes"] },
  ])("$id", ({ detail, want }) => expect(outcomeChoices(detail)).toEqual(want));

  const fix = (round: { n: number; max: number } | null) => ({ gate: "review", node: "impl", then: [], round });
  it.each<{ id: string; detail: WorkItemDetail; fix: Parameters<typeof outcomeChoices>[1]; want: ReturnType<typeof outcomeChoices> }>([
    { id: "a gate out of fix rounds", detail: item({ pending_gate: "review" }), fix: fix({ n: 4, max: 3 }), want: ["comment", "approve"] },
    { id: "a gate on its last round", detail: item({ pending_gate: "review" }), fix: fix({ n: 3, max: 3 }), want: ["request_changes", "comment", "approve"] },
    { id: "no gate, out of fix rounds", detail: item(), fix: fix({ n: 4, max: 3 }), want: { refused: "No fix rounds left." } },
    { id: "no gate, no target known", detail: item(), fix: null, want: { refused: "Kraft could not say where the work would restart." } },
  ])("$id", ({ detail, fix: f, want }) => expect(outcomeChoices(detail, f)).toEqual(want));
});

it.each<[Outcome, number, string | undefined, boolean]>([
  ["approve", 0, undefined, true],
  ["request_changes", 0, "  ", false],
  ["request_changes", 0, "fix it", true],
  ["comment", 2, undefined, true],
  ["comment", 0, undefined, false],
])("canSend(%s, %i, %j) is %s", (o, n, s, want) => expect(canSend(o, n, s)).toBe(want));

describe("submitMessage", () => {
  const ctx = { id: "K-1", n: 2, gate: "spec" as string | null, fixNode: "implement" as string | null, paused: false };
  const gateless = (action: "retried" | "rerun" | "queued"): ReviewResult =>
    ({ review_id: "r", outcome: "request_changes", gate: null, target: "implement", target_reason: "requested", action });
  it.each<{ id: string; outcome: Outcome; r: ReviewResult; c?: Partial<typeof ctx>; want: string }>([
    { id: "comment at a gate", outcome: "comment", r: { review_id: "r", outcome: "comment", gate: "spec", reply_agent: true }, want: "2 threads sent · gate stays open" },
    { id: "comment, one thread, no gate", outcome: "comment", r: { review_id: "r", outcome: "comment", gate: null, reply_agent: false }, c: { n: 1 }, want: "1 thread sent · the item keeps going" },
    { id: "approve", outcome: "approve", r: item(), want: "Approved · spec passed" },
    { id: "request changes at a gate", outcome: "request_changes", r: item(), want: "Requested changes · 2 threads sent to implement" },
    { id: "at a gate, no fix node known", outcome: "request_changes", r: item(), c: { fixNode: null }, want: "Requested changes · 2 threads sent to the fix node" },
    { id: "gateless rerun", outcome: "request_changes", r: gateless("rerun"), c: { gate: null }, want: "Requested changes · 2 threads sent to implement · running again" },
    { id: "gateless retried", outcome: "request_changes", r: gateless("retried"), c: { gate: null }, want: "Requested changes · 2 threads sent to implement · retried" },
    {
      id: "queued on a paused item", outcome: "request_changes", r: gateless("queued"), c: { gate: null, paused: true },
      want: "Requested changes · 2 threads sent to implement · queued. K-1 stays paused until you resume it.",
    },
  ])("$id", ({ outcome, r, c, want }) => expect(submitMessage(outcome, r, { ...ctx, ...c })).toBe(want));
});
