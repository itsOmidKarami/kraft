import { until } from "../../format";
import type { ChainNode, WorkItem } from "../../types";
import type { GlyphKind, GlyphState } from "../graph/types";
import { groupOf } from "./model";

/** What a board row says and offers (W6 brief B.4, B.5, B.7), from the
 *  server's `display_status` and `stop` and the raw row, one table each. */

type Row = Pick<WorkItem, "display_status" | "stop" | "current_node_id" | "progress" | "step" | "mr_ref" | "fallback" | "pending_gate" | "chain_definition" | "retry_at"> & { status?: WorkItem["status"] };

const nodeOf = (i: Row) => i.stop?.node ?? i.current_node_id ?? "";

/** A node id as words for a row's tail: `merge_request` reads "merge request". */
const nodeWords = (id: string) => id.replace(/_/g, " ");

/** A gate id as what it decides: `spec_approval` reads "spec", so the row says "approve spec". */
const gateWords = (id: string) => nodeWords(id.replace(/_approval$/, ""));

/** The reason tail after the meta: one short line per status and stop kind. */
export function reasonTail(i: Row, now = Date.now()): string {
  const node = nodeWords(nodeOf(i));
  const tail = (() => {
    switch (i.display_status) {
      case "needs_you":
        switch (i.stop?.kind) {
          case "gate": return `approve ${gateWords(i.pending_gate ?? nodeOf(i))}`;
          case "question": return `agent asks: ${(i.stop.reason ?? "").replace(/^needs_context:\s*/, "")}`.trim();
          default: return i.stop?.reason ?? `waiting for you at ${node}`;
        }
      case "failed": return `failed at ${node}`;
      case "paused": return i.current_node_id ? `paused at ${node}` : "created paused";
      case "running":
        if (i.step) return i.step.name && i.step.task ? `${i.step.index} of ${i.step.count} · ${i.step.name} › ${i.step.task}` : `${node} · step ${i.step.index} of ${i.step.count}`;
        return i.progress ? `${node} · task ${i.progress.current} of ${i.progress.total}` : node;
      case "waiting": {
        const at = i.stop?.resume_at ?? i.retry_at;
        return at ? `retry ${until(at, now)}` : `waiting at ${node}`;
      }
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
  | { label: string; kind: "peek"; tab: "overview" | "config" }
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
    // A row carries neither the stop's limit nor the item's spend, so it cannot tell the item's own cap from a daily
    // or token one the server will not raise: the peek's banner can (`budgetRaise`), and offers the raise or Retry.
    case "budget": return { label: "Open", kind: "peek", tab: "overview" };
    default: return { label: "Open", kind: "peek", tab: "overview" };
  }
}

/** The row's NodeGlyph: the current node's kind and icon, its state from the status. */
export function glyphOf(i: Row): { kind: GlyphKind; icon?: string; state: GlyphState } {
  const nodes: ChainNode[] = i.chain_definition?.nodes ?? [];
  const n = nodes.find((x) => x.id === i.current_node_id) ?? nodes[nodes.length - 1];
  const kind: GlyphKind = n?.kind === "gate" ? "gate" : "exec";
  const icon = i.display_status === "cancelled" || (i.display_status === "archived" && i.status === "abandoned") ? "ban" : n && (n.steps?.length ?? 0) > 1 ? "layers" : undefined;
  const state: GlyphState = (() => {
    switch (i.display_status) {
      case "failed": return "failed";
      case "escalated": return "esc";
      case "done": return "done";
      case "cancelled": return "ghost";
      case "archived": return i.status === "abandoned" ? "ghost" : "done";
      case "running": case "waiting": return "current";
      case "paused": return i.current_node_id ? "amber" : "todo";
      default: return groupOf(i) === "needs" ? "amber" : "current";
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
  return nodes.map((n, k) => ({
    gate: n.kind === "gate",
    state: ended || (at >= 0 && k < at) ? "done" : at >= 0 && k === at ? (hot ? "hot" : "current") : "todo",
  }));
}
