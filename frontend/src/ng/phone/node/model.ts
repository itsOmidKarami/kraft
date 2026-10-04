import type { ChainNode as ApiNode, KraftEvent } from "../../../types";
import type { ChainNode } from "../../graph/layout";
import { rejectTarget } from "../../item/graph";
import { gateSkipped } from "../../item/events";
import type { ItemDetail } from "../../item/useItem";
import { retryable, skippable } from "../../item/status";
import { attemptsAt, capAt, materialized } from "../../item/chainValues";
import { isEscalation } from "../../item/nodeGraph";
import { taskName } from "../../item/paths";
import { elapsed, nodeRunSpan } from "../../../format";

/** The node screen's decisions, pure (W17 brief D). */

export type NodeActId = "pause" | "resume" | "skip" | "retry-node" | "retry-from" | "review";
export interface NodeAct {
  id: NodeActId;
  label: string;
}
const a = (id: NodeActId, label: string): NodeAct => ({ id, label });

/** The bottom bar of a node: one pair (Decisions §5 over the prototype, GAP §5.1).
 *  Pause · Skip while the node runs, Skip · Retry once it has stopped, **no
 *  Retry while it runs**; a done node offers Retry from here; a waiting gate
 *  offers the decision; a node not reached, or an ended item, has no bar.
 *  Retry only on an item the server would retry (`retryable`): a paused one
 *  has Skip · Resume, and a waiting one Pause · Skip, a rate-limited one Pause alone (R10b-01). */
export function nodeBar(item: ItemDetail, node: ApiNode, graph: ChainNode): { secondary: NodeAct | null; primary: NodeAct | null } {
  const none = { secondary: null, primary: null };
  const status = item.display_status ?? "running";
  if (status === "done" || status === "cancelled" || status === "archived") return none;
  if (node.kind === "gate") return item.pending_gate === node.id ? { secondary: null, primary: a("review", "Review and decide") } : none;
  if (graph.state === "done") return retryable(item) ? { secondary: null, primary: a("retry-from", "Retry from here") } : none;
  if (graph.state !== "current" && graph.state !== "failed") return none;
  // A rate-limited node takes Pause only: /skip refuses it until it is paused (R10b-01's follow-up).
  // An escalation turn runs: /pause and /skip refuse it, and a human's Retry outranks the turn (R11b-01).
  if (status === "escalated") return { secondary: null, primary: a("retry-node", "Retry node") };
  if (status === "running" || status === "waiting") return skippable(item) ? { secondary: a("pause", "Pause"), primary: a("skip", "Skip node") } : { secondary: null, primary: a("pause", "Pause") };
  if (status === "paused") return { secondary: a("skip", "Skip node"), primary: a("resume", "Resume") };
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
    { title: "you", text: decided ? "approved" : waiting ? "waiting for your decision" : gateSkipped(events, gate.id) ? "skipped, no decision needed" : "decides when the chain reaches it" },
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

/** "round 2 of 3": the round a looping node is in, of the attempts it is allowed; the item's own count (a node override, then its policy) outranks the chain's. */
export function fixLoopWords(item: ItemDetail, node: ApiNode): string | null {
  if (!node.fix_loop) return null;
  const m = materialized(item);
  const max = item.node_overrides?.[node.id]?.attempts ?? (m ? attemptsAt(m, node.id, item.policy_override)?.value : null);
  const rounds = Math.max(0, ...item.worker_sessions.filter((s) => s.node_id === node.id && !isEscalation(s)).map((s) => s.round));
  return rounds ? `round ${rounds + 1}${max != null ? ` of ${max}` : ""}` : `not looped${max != null ? ` · up to ${max} attempts` : ""}`;
}

/** "about 12m of 45m": how long the node has been going against its wall-clock cap; null with no cap or before it started. */
export function wallWords(item: ItemDetail, node: ApiNode, events: KraftEvent[], now: number): string | null {
  const m = materialized(item);
  const own = item.node_overrides?.[node.id]?.wall_clock_s;
  const cap = own != null ? own / 60 : m ? capAt(m, node.id, "total_time_cap_minutes", item.policy_override).value : null;
  const span = nodeRunSpan(node.id, events, item.worker_sessions);
  if (cap == null || !span) return null;
  // About: the node's run span, not the clock the server enforces the cap with (that one is not in the API).
  return `about ${elapsed((span.to ? Date.parse(span.to) : now) - Date.parse(span.from))} of ${elapsed(cap * 60_000)}`;
}

/** A task id as a person says it: "repair pass", not repair_pass. */
export const plainName = (path: string) => taskName(path).replace(/_/g, " ");
