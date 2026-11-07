import { nameOf, slicesDone } from "../board/rowText";
import { elapsed, elapsedBetween } from "../../format";
import type { ChainNode as ApiNode, KraftEvent, WorkerSession } from "../../types";
import type { ChainArc, ChainNode } from "../graph/layout";
import { escalationsIn, isEscalation, passOf } from "./nodeGraph";
import { materialized, nodeTaskKind } from "./chainValues";
import { cursorOf, within } from "./cursor";
import { isScopeTask } from "./scopeView";
import { waitTitle } from "./sharedWords";
import type { ItemDetail } from "./useItem";

const kindOf = (n: ApiNode) => n.kind ?? "exec";

/** How many times a node ran, its ×N badge: the highest attempt of any of its tasks. A task's `attempt` counts its
 *  sessions, and the changed-test-scope task runs one per scope, so it would count every scope of every round: it
 *  ran once per round instead, a run being its sessions while the round stays the same, in the order they started
 *  (a re-measure after `on_failure`, round -1, belongs to the run before it). */
function timesRun(item: ItemDetail, work: WorkerSession[]): number {
  const byPath = new Map<string, WorkerSession[]>();
  for (const s of work) byPath.set(s.hook_point, [...(byPath.get(s.hook_point) ?? []), s]);
  return Math.max(0, ...[...byPath].map(([path, ss]) => {
    if (!isScopeTask(item, path)) return Math.max(...ss.map((s) => s.attempt));
    const rounds = [...ss].sort((a, b) => a.created_at.localeCompare(b.created_at)).map((s) => s.round).filter((r) => r >= 0);
    return rounds.filter((r, i) => i === 0 || r !== rounds[i - 1]).length;
  }));
}

/** The node a gate rejects to: its own `reject_to`, else the nearest earlier exec node or group (R23; the server's
 *  `preceding_exec_node`). */
export function rejectTarget(nodes: ApiNode[], gate: string): string | null {
  const i = nodes.findIndex((n) => n.id === gate);
  if (i < 0) return null;
  if (nodes[i].reject_to) return nodes[i].reject_to!;
  for (let j = i - 1; j >= 0; j--) if (kindOf(nodes[j]) !== "gate") return nodes[j].id;
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
  const at = cursorOf(api, item.current_node_id);
  const cur = finished ? api.length : (at?.index ?? -1);
  const stop = item.stop;
  const capped = stop && (stop.kind === "cap" || stop.kind === "budget") ? stop.node : null;
  const deciders = gateDeciders(events);
  const byNode = new Map((item.usage?.by_node ?? []).map((r) => [r.node, r]));
  const frozen = materialized(item);

  const drawn = (n: ApiNode, i: number): ChainNode => {
    // A group's sessions are its body nodes' (`slices.implementation`).
    const ss = item.worker_sessions.filter((s) => within(n.id, s.node_id));
    const work = ss.filter((s) => !isEscalation(s));
    const attempt = timesRun(item, work);
    const out: ChainNode = { id: n.id, kind: kindOf(n), icon: (n.steps?.length ?? 0) > 1 ? "layers" : undefined, taskKind: nodeTaskKind(frozen, n.id), esc: escalationsIn(item, n.id).length > 0 && capped !== n.id, attempt: attempt || undefined };
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
    if (status === "needs_you") return { ...out, state: "current", sub: "needs you", subTone: "amber", needs: true };
    // The attempt in flight: the latest session to start on the node.
    const started = work.map((s) => s.started_at).filter(Boolean).sort().at(-1);
    const step = item.summary?.step;
    const sub = [step ? `${step.index}/${step.count}` : "", started ? elapsedBetween(started, null, now) : ""].filter(Boolean).join(" · ");
    // A waiting item's node runs nothing: it waits on CI, a machine or the provider, and says so where words are shown (R11b-04).
    const wait = status === "waiting" ? (stop?.kind === "rate_limit" ? "waiting · rate limit" : stop?.kind === "wait" ? waitTitle(stop.reason).replace(/^W/, "w") : stop?.kind === "slices" ? "waiting for slices" : "waiting") : undefined;
    return { ...out, state: "current", running: status === "running" || status === "escalated", sub: sub || undefined, subTone: "amber", ...(wait && { wait }) };
  };
  // A group (slices) is one node whose body the strip opens: each body node by its path, in the group's state until the
  // run stands inside it (`current_node_id` is `group.node`), the ones before it done and the ones after not started.
  // A body node's id is already its path (`slices.implementation`).
  const nodes = api.map((n, i): ChainNode => {
    const d = drawn(n, i);
    if (kindOf(n) !== "group") return d;
    const j0 = i === cur ? (at?.innerIndex ?? -1) : -1;
    const body = (n.nodes ?? []).map((b, j): ChainNode => {
      const state = d.state === "done" || d.state === "todo" ? d.state : j0 < 0 ? "plain" : j < j0 ? "done" : j === j0 ? d.state : "todo";
      return { id: b.id, kind: "exec", state, ...(j === j0 && { running: d.running, paused: d.paused, needs: d.needs }) };
    });
    return { ...d, body, summary: slicesDone(item.slices) || undefined };
  });

  const loops: ChainArc[] = api.flatMap((n, i) => {
    const rounds = Math.max(0, ...passOf(item, n.id).map((s) => s.round));
    if (!n.fix_loop || rounds < 1 || i > cur) return [];
    return [{ kind: "loop", node: n.id, tone: capped === n.id ? "red" : i === cur ? "active" : "idle", label: `round ${rounds + 1}` }];
  });
  const rebases: ChainArc[] = api.flatMap((n) => (n.rebase_bounce_to ? [{ kind: "rebase", from: n.id, to: n.rebase_bounce_to, label: `rebase → ${nameOf(api, n.rebase_bounce_to)}` }] : []));
  return {
    nodes,
    arcs: (selected) => {
      const gate = selected && api.find((n) => n.id === selected && kindOf(n) === "gate");
      // A target inside a group (`slices.implementation`) bounces to the group the strip draws.
      const target = gate ? rejectTarget(api, gate.id) : null;
      const to = target ? (cursorOf(api, target)?.node.id ?? target) : null;
      return [...loops, ...rebases, ...(gate && to ? [{ kind: "reject" as const, from: gate.id, to }] : [])];
    },
  };
}
