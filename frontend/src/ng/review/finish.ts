/** What Finish your review and the bottom bar say and allow, from data only (W8 G). Pure. */
import type { FixTarget, ReviewComment, ReviewOutcome, ReviewThread, WorkItem } from "../../types";
import { displayName } from "../board/rowText";
import { spentLine } from "../item/status";

/** The server's `open_must_fix`: a must-fix thread not resolved, drafts included. */
export const openMustFix = (threads: ReviewThread[]) => threads.filter((t) => t.label === "must_fix" && t.state !== "resolved").length;

/** Approve is possible only while the item waits at this gate and no must-fix is open (G.4). */
export function approveBlock(item: Pick<WorkItem, "pending_gate">, gate: string | null, threads: ReviewThread[]): string | null {
  if (!gate || item.pending_gate !== gate) return "Available when the item is waiting at a gate.";
  if (openMustFix(threads)) return "Unavailable while must-fix threads are open.";
  return null;
}

/** What approving this gate does next, while the item waits at it: the node it starts, the gates after it, the spend so far. */
export function gateConsequence(item: Pick<WorkItem, "pending_gate" | "chain_definition" | "budget_cap" | "running_time">, gate: string | null): string | null {
  if (!gate || item.pending_gate !== gate) return null;
  const nodes = item.chain_definition?.nodes ?? [];
  // A legacy chain names its gate on the node in front of it (`gate_after`), a V1 chain gives it a node.
  const at = nodes.findIndex((n) => n.id === gate || n.gate_after === gate);
  const next = at < 0 ? undefined : nodes[at + 1];
  const left = nodes.slice(at + 1).filter((n) => n.kind === "gate" || n.gate_after).length;
  const spent = spentLine(item);
  return [
    next ? `Approving starts ${displayName(next)}` : "Approving finishes the chain",
    at < 0 ? null : left ? `${left} ${left === 1 ? "gate" : "gates"} left` : "no gate after this",
    spent ? `${spent} spent` : null,
  ].filter(Boolean).join(" · ");
}

/** The words for sending a gate back, one source for the desktop bar, dialog and confirm, and the phone screens. */
export const REQUEST_CHANGES = "Request changes";

const ENDED = new Set(["done", "cancelled", "archived"]);

/** What the next review would send: draft threads and your draft replies. */
export function drafts(threads: ReviewThread[]): { thread: ReviewThread; comment: ReviewComment }[] {
  const out: { thread: ReviewThread; comment: ReviewComment }[] = [];
  for (const t of threads)
    for (const c of t.comments) if (c.draft && c.author === "you") out.push({ thread: t, comment: c });
  return out;
}

/** First, second, …, last when the path is longer than four (prototype). */
const shorten = (ids: string[]) => (ids.length > 4 ? [ids[0], ids[1], "…", ids[ids.length - 1]] : ids);

export interface OutcomeRow {
  key: ReviewOutcome;
  label: string;
  /** The sentence; `mono` parts are node ids. */
  parts: { t: string; mono?: boolean }[];
  disabled: boolean;
}

/** The three outcomes with their sentences (prototype `reviewVals`, Review Flow §4). */
export function outcomes(item: Pick<WorkItem, "pending_gate" | "display_status">, gate: string | null, threads: ReviewThread[], fix: FixTarget | null): OutcomeRow[] {
  const atGate = !!gate && item.pending_gate === gate;
  const ended = ENDED.has(item.display_status ?? "");
  const noRounds = !!fix?.round && fix.round.n > fix.round.max;
  let request: OutcomeRow["parts"];
  if (ended) request = [{ t: "Unavailable once the item is done, cancelled or archived." }];
  else if (!fix) request = [{ t: "Finding the node this would send the threads to…" }];
  else {
    const path = shorten(fix.gate ? [...fix.then, fix.gate] : fix.then);
    request = [
      { t: drafts(threads).length ? "Sends these threads back to " : "Sends your note back to " }, { t: fix.node, mono: true }, { t: ", the node this item runs a fix round on." },
      ...(path.length ? [{ t: " Chain resumes from there: " }, { t: path.join(" → "), mono: true }, { t: "." }] : []),
      ...(fix.round ? [{ t: noRounds ? " No fix rounds left." : ` Fix round ${fix.round.n} of ${fix.round.max}.` }] : []),
      ...(!fix.gate && fix.reason ? [{ t: ` Why this node: ${fix.reason}.` }] : []),
    ];
  }
  const block = approveBlock(item, gate, threads);
  return [
    { key: "request_changes", label: REQUEST_CHANGES, parts: request, disabled: ended || noRounds || !fix },
    { key: "comment", label: "Comment only", parts: [{ t: atGate ? "Agents answer questions in-thread. No code runs; the gate stays open." : "Agents answer questions in-thread. No code runs and the item keeps going." }], disabled: false },
    { key: "approve", label: "Approve", parts: [{ t: block ?? `Marks ${gate} approved, same as Approve on the gate.` }], disabled: !!block },
  ];
}

/** The bottom bar's words (prototype 496–503). */
export function barText(threads: ReviewThread[], item: Pick<WorkItem, "pending_gate">, gate: string | null) {
  const published = threads.filter((t) => !t.draft);
  const agentReplies = threads.flatMap((t) => t.comments).filter((c) => c.author !== "you").length;
  if (!published.length) {
    const pend = threads.filter((t) => t.draft);
    return {
      published: false,
      title: "Your review",
      text: pend.length
        ? `${pend.length} pending, ${pend.filter((t) => t.label === "must_fix").length} must fix, ${pend.filter((t) => !t.label).length} unlabeled`
        : `No comments yet${!!gate && item.pending_gate === gate ? " · Ready to approve" : ""}`,
      agent: agentReplies ? `${agentReplies} agent ${agentReplies === 1 ? "reply" : "replies"}` : null,
      pending: pend.length,
    };
  }
  const resolved = threads.filter((t) => t.state === "resolved").length;
  const atGate = !!gate && item.pending_gate === gate;
  // A later round's comments wait as drafts too: the bar says so, and Finish review sends them.
  const pending = drafts(threads).length;
  return {
    published: true,
    title: `${resolved} of ${threads.length} resolved`,
    text: `${pending ? `${pending} pending · ` : ""}${!atGate ? "Not at a gate" : openMustFix(threads) ? "Approve unlocks when must-fix threads are resolved" : "Ready to approve"}`,
    agent: null,
    pending,
  };
}
