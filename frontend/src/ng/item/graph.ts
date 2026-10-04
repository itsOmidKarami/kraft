import { elapsed, elapsedBetween } from "../../format";
import type { ChainNode as ApiNode, KraftEvent } from "../../types";
import type { ChainArc, ChainNode } from "../graph/layout";
import { isEscalation, passOf } from "./nodeGraph";
import { materialized, nodeTaskKind } from "./chainValues";
import type { ItemDetail } from "./useItem";

const kindOf = (n: ApiNode) => n.kind ?? "exec";

/** The node a gate rejects to: its own `reject_to`, else the nearest earlier exec node (R23). */
export function rejectTarget(nodes: ApiNode[], gate: string): string | null {
  const i = nodes.findIndex((n) => n.id === gate);
  if (i < 0) return null;
  if (nodes[i].reject_to) return nodes[i].reject_to!;
  for (let j = i - 1; j >= 0; j--) if (kindOf(nodes[j]) === "exec") return nodes[j].id;
  return null;
}

/** Who passed each gate, from its last gate_approved: an agent's verdict is "auto", a person's "you". */
function gateDeciders(events: KraftEvent[]) {
  const by = new Map<string, "auto" | "you">();
  for (const e of events) if (e.type === "gate_approved") by.set(String(e.payload.gate ?? e.node_id), e.payload.by === "agent" || e.payload.by === "kraft" ? "auto" : "you");
  return by;
}

/** Item → what the chain canvas draws (Decisions §5 Level 2, §7). Pure: the
 *  item, the events read for it (gate deciders), and now. */
export function chainGraph(item: ItemDetail, events: KraftEvent[], now = Date.now()): { nodes: ChainNode[]; arcs: (selected?: string) => ChainArc[] } {
  const api = item.chain_definition.nodes ?? [];
  const status = item.display_status;
  const finished = status === "done" || status === "archived";
  const cur = finished ? api.length : api.findIndex((n) => n.id === item.current_node_id);
  const stop = item.stop;
  const capped = stop && (stop.kind === "cap" || stop.kind === "budget") ? stop.node : null;
  const deciders = gateDeciders(events);
  const byNode = new Map((item.usage?.by_node ?? []).map((r) => [r.node, r]));
  const frozen = materialized(item);

  const nodes = api.map((n, i): ChainNode => {
    const ss = item.worker_sessions.filter((s) => s.node_id === n.id);
    const work = ss.filter((s) => !isEscalation(s));
    const attempt = Math.max(0, ...work.map((s) => s.attempt));
    const out: ChainNode = { id: n.id, kind: kindOf(n), icon: (n.steps?.length ?? 0) > 1 ? "layers" : undefined, taskKind: nodeTaskKind(frozen, n.id), esc: ss.some(isEscalation) && capped !== n.id, attempt: attempt || undefined };
    if (cur < 0 || i > cur) return { ...out, state: "todo" };
    if (i < cur) {
      const ms = byNode.get(n.id)?.wall_ms;
      return { ...out, state: "done", meta: out.kind === "gate" ? deciders.get(n.id) : ms ? elapsed(ms) : undefined };
    }
    // The node the item stands on.
    if (status === "failed") return { ...out, state: "failed", meta: "failed", metaTone: "red" };
    if (capped === n.id) return { ...out, state: "current", capped: true, attemptStopped: true, meta: "capped", metaTone: "red" };
    if (status === "cancelled") return { ...out, state: "plain", meta: "cancelled" };
    if (status === "paused") return { ...out, state: "current", paused: true, sub: "paused", subTone: "muted" };
    if (status === "needs_you") return { ...out, state: "current", sub: "needs you", subTone: "amber" };
    // The attempt in flight: the latest session to start on the node.
    const started = work.map((s) => s.started_at).filter(Boolean).sort().at(-1);
    const step = item.summary?.step;
    const sub = [step ? `${step.index}/${step.count}` : "", started ? elapsedBetween(started, null, now) : ""].filter(Boolean).join(" · ");
    // A waiting item's node runs nothing: it waits on CI or the provider, and says so where words are shown (R11b-04).
    const wait = status === "waiting" ? (stop?.kind === "rate_limit" ? "waiting · rate limit" : stop?.kind === "wait" ? "waiting on CI" : "waiting") : undefined;
    return { ...out, state: "current", running: status === "running" || status === "escalated", sub: sub || undefined, subTone: "amber", ...(wait && { wait }) };
  });

  const loops: ChainArc[] = api.flatMap((n, i) => {
    const rounds = Math.max(0, ...passOf(item, n.id).map((s) => s.round));
    if (!n.fix_loop || rounds < 1 || i > cur) return [];
    return [{ kind: "loop", node: n.id, tone: capped === n.id ? "red" : i === cur ? "active" : "idle", label: `round ${rounds + 1}` }];
  });
  const rebases: ChainArc[] = api.flatMap((n) => (n.rebase_bounce_to ? [{ kind: "rebase", from: n.id, to: n.rebase_bounce_to, label: `rebase → ${n.rebase_bounce_to}` }] : []));
  return {
    nodes,
    arcs: (selected) => {
      const gate = selected && api.find((n) => n.id === selected && kindOf(n) === "gate");
      const to = gate ? rejectTarget(api, gate.id) : null;
      return [...loops, ...rebases, ...(gate && to ? [{ kind: "reject" as const, from: gate.id, to }] : [])];
    },
  };
}
