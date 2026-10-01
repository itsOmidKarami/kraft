import type { ChainNode as ApiNode, KraftEvent } from "../../../types";
import type { ChainNode } from "../../graph/layout";
import { rejectTarget } from "../../item/graph";
import type { ItemDetail } from "../../item/useItem";

/** The node screen's decisions, pure (W17 brief D). */

export type NodeActId = "pause" | "skip" | "retry-node" | "retry-from" | "review";
export interface NodeAct {
  id: NodeActId;
  label: string;
}
const a = (id: NodeActId, label: string): NodeAct => ({ id, label });

/** The bottom bar of a node: one pair (Decisions §5 over the prototype, GAP §5.1).
 *  Pause · Skip while the node runs, Skip · Retry once it has stopped, **no
 *  Retry while it runs**; a done node offers Retry from here; a waiting gate
 *  offers the decision; a node not reached, or an ended item, has no bar. */
export function nodeBar(item: ItemDetail, node: ApiNode, graph: ChainNode): { secondary: NodeAct | null; primary: NodeAct | null } {
  const none = { secondary: null, primary: null };
  const status = item.display_status ?? "running";
  if (status === "done" || status === "cancelled" || status === "archived") return none;
  if (node.kind === "gate") return item.pending_gate === node.id ? { secondary: null, primary: a("review", "Review and decide") } : none;
  if (graph.state === "done") return { secondary: null, primary: a("retry-from", "Retry from here") };
  if (graph.state !== "current" && graph.state !== "failed") return none;
  if (status === "running" || status === "escalated") return { secondary: a("pause", "Pause"), primary: a("skip", "Skip node") };
  return { secondary: a("skip", "Skip node"), primary: a("retry-node", "Retry node") };
}

export interface PathRow {
  title: string;
  text: string;
  chip?: { word: string; tone: "ok" | "bad" | "muted" };
}

/** A gate's review path (prototype `flow`): the agent reviewer when the gate has one, you, and where a rejection goes. The verdict is the agent's own event, never invented. */
export function reviewPath(item: ItemDetail, gate: ApiNode, events: KraftEvent[]): PathRow[] {
  const verdict = [...events].reverse().find((e) => (e.type === "gate_approved" || e.type === "gate_rejected") && (e.payload.gate ?? e.node_id) === gate.id && e.payload.by === "agent");
  const target = rejectTarget(item.chain_definition.nodes, gate.id);
  const waiting = item.pending_gate === gate.id;
  const decided = [...events].reverse().find((e) => e.type === "gate_approved" && (e.payload.gate ?? e.node_id) === gate.id && e.payload.by !== "agent");
  return [
    ...(gate.auto_escalate
      ? [{ title: "auto_review", text: "An agent reads it before you do. A decision you make meanwhile wins.", ...(verdict && { chip: verdict.type === "gate_approved" ? { word: "approve", tone: "ok" as const } : { word: "reject", tone: "bad" as const } }) }]
      : []),
    { title: "you", text: decided ? "approved" : waiting ? "waiting for your decision" : "decides when the chain reaches it" },
    { title: "on reject", text: target ? `Goes back to ${target} with your note.` : "Reopens the gate with your note." },
  ];
}

/** The overrides this item carries on a node, in words ("changed for this item"). */
export function overrideWords(item: ItemDetail, node: string): string | null {
  const o = item.node_overrides?.[node];
  if (!o) return null;
  const parts = [
    o.attempts != null ? `fix attempts ${o.attempts}` : null,
    o.wall_clock_s != null ? `wall clock ${Math.round(o.wall_clock_s / 60)}m` : null,
    o.auto_escalate != null ? `auto-escalate ${o.auto_escalate ? "on" : "off"}` : null,
    o.auto_escalate_stuck != null ? `auto-escalate when stuck ${o.auto_escalate_stuck ? "on" : "off"}` : null,
    o.auto_escalate_delay_s != null ? `escalation delay ${o.auto_escalate_delay_s}s` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}
