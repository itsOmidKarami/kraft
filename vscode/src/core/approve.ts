import { gateWords, nodeWords } from "../../../frontend/src/ng/board/rowText";
import { ApiError, type Api, type Artifact, type WorkItem } from "./api";
import type { ShownRenders } from "./shown";

export async function approveGate(api: Api, id: string, gate: string, digest: string | undefined): Promise<void> {
  await api.approve(id, gate, digest);
}

/**
 * Approve with the digest of the render the person's tab showed. With none shown
 * and a digest to bind to, show the document instead and do not approve.
 * `confirm` only asks, and runs only once an approve is about to be sent;
 * `approved` runs after the server accepted it, so a failed approve loses nothing.
 * `send` sends it with the digest (by default the gate's approve route); it
 * may still decline, answering false, and then nothing is approved.
 */
export async function approveOrRead(
  api: Api,
  shown: ShownRenders,
  id: string,
  gate: string,
  show: (a: Artifact) => Promise<void>,
  confirm: () => Promise<boolean> = async () => true,
  approved: () => PromiseLike<void> = async () => {},
  send: (digest: string | undefined) => Promise<boolean> = (digest) => approveGate(api, id, gate, digest).then(() => true),
): Promise<"approved" | "read-first" | "cancelled"> {
  const go = async (digest: string | undefined) => {
    if (!(await confirm()) || !(await send(digest))) return "cancelled";
    await approved();
    return "approved";
  };
  const seen = shown.get(id, gate);
  if (seen) return go(seen.digest);
  const a = await api.getArtifact(id);
  if (a?.digest) {
    await show(a);
    shown.record(id, gate, a.digest);
    return "read-first";
  }
  return go(undefined);
}

/** Whether `e` is the server saying the revision moved under the person's feet. */
export function isStaleRevision(e: unknown): boolean {
  return (
    e instanceof ApiError &&
    e.status === 409 &&
    (e.detail.includes("the revision changed since you viewed it") ||
      e.detail.includes("needs the digest of the one you reviewed"))
  );
}

/**
 * The line after an approve, in the board's words ("approve spec"), for both
 * routes. `after` is the item as the server now has it: when it has moved on
 * from `gate`, the line says where, and says nothing it does not know.
 */
export function approvedMessage(
  title: string,
  gate: string,
  after?: Pick<WorkItem, "current_node_id" | "display_status" | "pending_gate"> | null,
): string {
  const head = `Approved: ${title} · approve ${gateWords(gate)}`;
  if (after?.display_status === "done") return `${head} · done`;
  const at = after?.current_node_id;
  return at && at !== gate && after.pending_gate !== gate ? `${head} · now ${nodeWords(at)}` : head;
}

const MERGES = /(^|\/)(merge|push)$/;

/**
 * What approving `gate` sets going that cannot be taken back, or null for an
 * ordinary gate. The server sends no "this gate merges" flag, so this reads the
 * item's own chain: the nodes after the gate, up to the next gate, naming a
 * `merge` or `push` node or task (the shipped chains' `final_review` is
 * followed by `mark_ready`, `external_approval`, then `merge`).
 */
export function gateConsequence(item: Pick<WorkItem, "chain_definition">, gate: string): "merge" | "push" | null {
  const nodes = item.chain_definition?.nodes ?? [];
  const at = nodes.findIndex((n) => n.id === gate);
  if (at < 0) return null;
  for (const n of nodes.slice(at + 1)) {
    if (n.kind === "gate" || n.gate_after) break;
    for (const name of [n.id, ...(n.tasks ?? [])]) {
      const m = MERGES.exec(name);
      if (m) return m[2] as "merge" | "push";
    }
  }
  return null;
}

/** The modal's question for a gate whose approval merges or pushes. */
export function consequenceQuestion(title: string, what: "merge" | "push"): string {
  return what === "merge" ? `Approve and merge ${title} into the target branch?` : `Approve and push ${title}?`;
}

/**
 * The one confirmation both approve routes (the gate's Approve and Submit Review)
 * share: asks only for a gate that merges or pushes, and answers whether to go on.
 * `ask` shows the modal with this button and answers whether it was clicked.
 */
export async function confirmConsequence(
  item: Pick<WorkItem, "title" | "chain_definition"> | undefined,
  gate: string,
  ask: (question: string, button: string) => Promise<boolean>,
): Promise<boolean> {
  const what = item && gateConsequence(item, gate);
  if (!what) return true;
  return ask(consequenceQuestion(item.title, what), what === "merge" ? "Approve and Merge" : "Approve and Push");
}
