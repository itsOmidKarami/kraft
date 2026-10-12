import type { ReviewThread } from "./api";

/** The server's `open_must_fix`: label must-fix, not resolved, drafts included; in its order. */
export const openMustFixThreads = (threads: ReviewThread[]) => threads.filter((t) => t.label === "must_fix" && t.state !== "resolved");

export type Refusal = { title: string; items: { tid: string; label: string; description: string; detail: string }[] };

const where = (t: ReviewThread) =>
  !t.file_path ? "the whole change" : t.start_line ? `${t.file_path}:${t.start_line}` : t.file_path;

/** Why Approve is refused, thread by thread; `null` when nothing blocks it. */
export function mustFixRefusal(threads: ReviewThread[]): Refusal | null {
  const open = openMustFixThreads(threads);
  const n = open.length;
  if (!n) return null;
  return {
    title: `Approve refused: ${n} must-fix thread${n === 1 ? " is" : "s are"} open`,
    items: open.map((t) => ({
      tid: t.id,
      label: where(t),
      description: (t.comments[0]?.body ?? "").trim().split("\n")[0],
      // A draft has nothing to resolve yet (the server 409s): it can only be relabelled or deleted.
      detail: t.draft ? "draft: change its label or delete it" : `${t.state}: resolve it once it is fixed`,
    })),
  };
}

export type ApproveOutcome = "refused" | "approved" | "read-first" | "cancelled";

/**
 * Approve's order: the item's threads, then the must-fix refusal, and only
 * with none does slice 03's read-first decision (and so any fetch or send) run.
 */
export async function approveFlow(steps: {
  threads(): Promise<ReviewThread[]>;
  refuse(r: Refusal): Promise<void>;
  readOrSend(): Promise<"approved" | "read-first" | "cancelled">;
}): Promise<ApproveOutcome> {
  const r = mustFixRefusal(await steps.threads());
  if (r) {
    await steps.refuse(r);
    return "refused";
  }
  return steps.readOrSend();
}
