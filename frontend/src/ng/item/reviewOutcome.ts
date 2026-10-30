import type { KraftEvent } from "../../types";

export type ReviewKind = "approve" | "reject" | "ready" | "undecided";

/** Each outcome's word and tone, the same in every view. Never "passed": the item page already says that of an agent clearing a gate. */
export const OUTCOME: Record<ReviewKind, { word: string; tone: "green" | "red" | "amber" }> = {
  approve: { word: "approve", tone: "green" },
  reject: { word: "reject", tone: "red" },
  ready: { word: "ready for you", tone: "green" },
  undecided: { word: "could not decide", tone: "amber" },
};

/** The agent reviewer's latest outcome at a gate since it was last requested, from its own events (never invented): an older round's verdict does not hide this one's. */
export function reviewOutcome(events: KraftEvent[], gate: string): { kind: ReviewKind; note: string } | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if ((e.payload.gate ?? e.node_id) !== gate) continue;
    if (e.type === "gate_requested") return null;
    if ((e.type === "gate_approved" || e.type === "gate_rejected") && e.payload.by === "agent")
      return { kind: e.type === "gate_approved" ? "approve" : "reject", note: String(e.payload.note ?? e.payload.reason ?? e.payload.summary ?? "").trim() };
    if (e.type === "gate_auto_review_skipped" && (e.payload.reason === "passed" || e.payload.reason === "undecided"))
      return { kind: e.payload.reason === "passed" ? "ready" : "undecided", note: String(e.payload.note ?? "").trim() };
  }
  return null;
}
