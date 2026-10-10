import { until } from "../../format";
import type { ChainNode, TaskProgress, WorkItem } from "../../types";
import type { GlyphKind, GlyphState } from "../graph/types";
import { groupOf } from "./model";

/** What a board row says and offers (W6 brief B.4, B.5, B.7), from the
 *  server's `display_status` and `stop` and the raw row, one table each. */

type Row = Pick<WorkItem, "display_status" | "stop" | "current_node_id" | "progress" | "step" | "mr_ref" | "fallback" | "pending_gate" | "chain_definition" | "retry_at"> & { status?: WorkItem["status"] };

const nodeOf = (i: Row) => i.stop?.node ?? i.current_node_id ?? "";

/** A node id as words for a row's tail: `merge_request` or `merge-request` reads "merge request". */
export const nodeWords = (id: string) => id.replace(/[_-]+/g, " ");

/** A gate id as what it decides: `spec_approval` reads "spec", so the row says "approve spec". */
export const gateWords = (id: string) => nodeWords(id.replace(/[_-]approval$/, ""));

/** Every sub-task of the plan done: a list with nothing left on it, not an empty or a board-only one. */
export const allDone = (p: Pick<TaskProgress, "tasks">) => !!p.tasks?.length && p.tasks.every((t) => t.state === "done");

/** Up to the first full stop that ends a sentence, not one inside "$0.04". */
const firstSentence = (s: string) => s.split(/\.(?:\s|$)/)[0];

/** The reason tail after the meta: one short line per status and stop kind. */
export function reasonTail(i: Row, now = Date.now()): string {
  const node = nodeWords(nodeOf(i));
  const tail = (() => {
    switch (i.display_status) {
      case "needs_you":
        switch (i.stop?.kind) {
          case "gate": return `approve ${gateWords(i.pending_gate ?? nodeOf(i))}`;
          case "question": return `agent asks: ${(i.stop.reason ?? "").replace(/^needs_context:\s*/, "")}`.trim();
          // A cap or budget stop's reason goes on to what was not interrupted: the row keeps its first sentence.
          case "cap":
          case "budget": return i.stop.reason ? firstSentence(i.stop.reason) : `waiting for you at ${node}`;
          default: return i.stop?.reason ?? `waiting for you at ${node}`;
        }
      case "failed": return `failed at ${node}`;
      case "paused": return i.current_node_id ? `paused at ${node}` : "created paused";
      case "running":
        if (i.step) return i.step.name && i.step.task ? `${i.step.index} of ${i.step.count} · ${i.step.name} › ${i.step.task}` : `${node} · step ${i.step.index} of ${i.step.count}`;
        // A finished plan (the detail keeps it past the node) has no task left to count.
        return i.progress && !allDone(i.progress) ? `${node} · task ${i.progress.current} of ${i.progress.total}` : node;
      case "waiting": {
        const at = i.stop?.resume_at ?? i.retry_at;
        // A CI wait checks again; only a rate limit retries (R11b-05).
        return at ? `${node} · ${i.stop?.kind === "wait" ? "next check" : "retry"} ${until(at, now)}` : `waiting at ${node}`;
      }
      case "queued": return "waiting to start";
      case "blocked": return "waiting on another item";
      case "escalated": return "escalation running";
      case "done": return i.mr_ref ? `merged !${i.mr_ref.number}` : "completed";
      case "cancelled": return "cancelled";
      default: return "";
    }
  })();
  const to = i.fallback?.to;
  return typeof to === "string" && to ? `${tail} · on ${to}` : tail;
}

export type RowAction =
  | { label: string; kind: "gate"; gate: string }
  | { label: string; kind: "peek"; tab: "overview" | "config"; budget?: boolean }
  | { label: string; kind: "resume" };

/** The one action a row offers, or none. */
export function rowAction(i: Row): RowAction | null {
  if (i.display_status === "failed") return { label: "Retry…", kind: "peek", tab: "overview" };
  if (i.display_status === "paused") return i.current_node_id ? { label: "Resume", kind: "resume" } : null;
  if (i.display_status !== "needs_you") return null;
  switch (i.stop?.kind) {
    case "gate": return { label: "Review to approve", kind: "gate", gate: i.pending_gate ?? nodeOf(i) };
    case "question": return { label: "Answer", kind: "peek", tab: "overview" };
    case "cap": return { label: "Raise cap", kind: "peek", tab: "config" };
    // A row carries neither the stop's limit nor its scope: the peek it opens picks the editor that raises this
    // cap (`budgetRaise`), or stays on the banner that says why the item can't, with Retry.
    case "budget": return { label: "Raise budget", kind: "peek", tab: "config", budget: true };
    // A stuck loop's way on is Retry, on the peek's card (R11a-01): the row said Open.
    case "stuck": return { label: "Retry…", kind: "peek", tab: "overview" };
    default: return { label: "Open", kind: "peek", tab: "overview" };
  }
}

/** The row's NodeGlyph: the current node's kind and icon, its state from the status. */
export function glyphOf(i: Row): { kind: GlyphKind; icon?: string; state: GlyphState } {
  const nodes: ChainNode[] = i.chain_definition?.nodes ?? [];
  const n = nodes.find((x) => x.id === i.current_node_id) ?? nodes[nodes.length - 1];
  const kind: GlyphKind = n?.kind === "gate" ? "gate" : "exec";
  const dropped = i.display_status === "cancelled" || (i.display_status === "archived" && i.status === "abandoned");
  const finished = i.display_status === "done" || (i.display_status === "archived" && !dropped);
  const icon = dropped ? "ban" : finished ? "check" : n && (n.steps?.length ?? 0) > 1 ? "layers" : undefined;
  const state: GlyphState = (() => {
    switch (i.display_status) {
      case "failed": return "failed";
      case "escalated": return "esc";
      case "done": return "done";
      case "cancelled": return "ghost";
      case "archived": return i.status === "abandoned" ? "ghost" : "done";
      case "running": case "waiting": return "current";
      case "paused": return i.current_node_id ? "amber" : "todo";
      default: {
        const g = groupOf(i);
        return g === "needs" ? "amber" : g === "not_started" ? "todo" : "current";
      }
    }
  })();
  return { kind, icon, state };
}

/** The tick strip: one per node, gates as diamonds (Ticks.tsx draws them).
 *  `run` is the composer's preview: a node that will run. */
export type Tick = { gate: boolean; state: "done" | "current" | "hot" | "todo" | "run" };

export function ticksOf(i: Row): Tick[] {
  const nodes = i.chain_definition?.nodes ?? [];
  const ended = i.display_status === "done" || i.display_status === "cancelled" || i.display_status === "archived";
  const at = nodes.findIndex((n) => n.id === i.current_node_id);
  const hot = groupOf(i) === "needs" || i.display_status === "escalated";
  const ticks: Tick[] = nodes.map((n, k) => ({
    gate: n.kind === "gate",
    state: ended || (at >= 0 && k < at) ? "done" : at >= 0 && k === at ? (hot ? "hot" : "current") : "todo",
  }));
  // An ended item reads as finished at a glance: the nodes between two gates are one dash (BD-6).
  return ended ? ticks.filter((t, k) => t.gate || !ticks[k - 1] || ticks[k - 1].gate) : ticks;
}
