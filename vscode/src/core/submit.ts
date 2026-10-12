import type { GatelessChanges, ReviewResult, WorkItemDetail } from "./api";

export type Outcome = "request_changes" | "comment" | "approve";
export const OUTCOME_LABEL: Record<Outcome, string> = { request_changes: "Request changes", comment: "Comment", approve: "Approve" };

const ENDED = new Set(["done", "cancelled", "archived"]);

/** What Submit Review offers: all three at a gate; with none, only Request changes. */
export function outcomeChoices(detail: WorkItemDetail | undefined): Outcome[] | { refused: string } {
  if (!detail) return { refused: "Kraft has not loaded this item yet." };
  if (ENDED.has(detail.display_status ?? "")) return { refused: "This item has ended; its review is closed." };
  return detail.pending_gate ? ["request_changes", "comment", "approve"] : ["request_changes"];
}

/** Approve sends with nothing to say; the others need a draft or a note. */
export function canSend(outcome: Outcome, draftCount: number, summary: string | undefined): boolean {
  return outcome === "approve" || draftCount > 0 || !!summary?.trim();
}

const ACTION: Record<GatelessChanges["action"], string> = { retried: "retried", rerun: "running again", queued: "queued" };

/** The web UI's words for a sent review (`FinishReview.tsx`); a gateless one names what the server did. */
export function submitMessage(
  outcome: Outcome,
  r: ReviewResult,
  ctx: { id: string; n: number; gate: string | null; fixNode: string | null | undefined; paused: boolean },
): string {
  const sent = ctx.n === 1 ? "1 thread" : `${ctx.n} threads`;
  if (outcome === "comment") return `${sent} sent · ${"gate" in r && r.gate ? "gate stays open" : "the item keeps going"}`;
  if (outcome === "approve") return `Approved · ${ctx.gate} passed`;
  if ("action" in r && "target" in r) {
    const stays = r.action === "queued" && ctx.paused ? `. ${ctx.id} stays paused until you resume it.` : "";
    return `Requested changes · ${sent} sent to ${r.target} · ${ACTION[r.action]}${stays}`;
  }
  return `Requested changes · ${sent} sent to ${ctx.fixNode ?? "the fix node"}`;
}
