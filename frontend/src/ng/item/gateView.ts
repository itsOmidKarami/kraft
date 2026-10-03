import type { ComponentProps } from "react";
import type { ChainNode, KraftEvent } from "../../types";
import type { GateView } from "../graph/GateView";
import type { Sel } from "../graph/usePaneSelection";
import { rejectTarget } from "./graph";
import { AUTO_REVIEW, sessionLook, sessionsOf } from "./nodeGraph";
import { gateDecision } from "./panes/GatePane";
import { materialized, nodeAt } from "./chainValues";
import { taskName } from "./paths";
import type { ItemDetail } from "./useItem";

type Props = ComponentProps<typeof GateView>;

/** What clicking a gate's reviewer selects: its `auto_review` task when the frozen chain has one, else the gate's first listed task. */
export function reviewerSel(item: ItemDetail, gate: ChainNode): Sel {
  const frozen = materialized(item);
  const t = gate.tasks[0];
  if (frozen && nodeAt(frozen, gate.id)?.auto_review) return { kind: "task", node: gate.id, step: AUTO_REVIEW, task: AUTO_REVIEW };
  return t ? { kind: "task", node: gate.id, step: t.split(".")[1], task: taskName(t) } : { kind: "node", node: gate.id };
}

/** A gate's node view for W3's GateView (Decisions §6 Gates): its auto_review
 *  task (when it declares one) with its run state and verdict, the diamond,
 *  the document it decides on, and the reject branch. */
export function gateView(item: ItemDetail, gate: ChainNode, events: KraftEvent[], now: number, sel: Sel, on: { doc: () => void; reject: (to: string) => void }): Pick<Props, "gate" | "reviewer" | "message" | "doc" | "reject" | "youSub"> {
  const pending = item.pending_gate === gate.id;
  const decided = gateDecision(events, gate.id);
  // The API's chain lists no task on a gate: its reviewer and message are in the frozen chain.
  const frozen = materialized(item);
  const own = frozen ? nodeAt(frozen, gate.id) : undefined;
  const path = own?.auto_review ? `${gate.id}.auto_review` : gate.tasks[0];
  const ids = item.chain_definition.nodes.map((n) => n.id);
  const ahead = !pending && !decided && ids.indexOf(item.current_node_id ?? "") < ids.indexOf(gate.id);
  const last = path ? sessionsOf(item, path).at(-1) : undefined;
  const verdict = [...events].reverse().find((e) => (e.type === "gate_approved" || e.type === "gate_rejected") && (e.payload.gate ?? e.node_id) === gate.id && e.payload.by === "agent");
  const to = rejectTarget(item.chain_definition.nodes, gate.id);
  return {
    gate: { id: gate.id, state: pending ? "current" : decided ? "done" : "todo", sel: sel.kind === "node" && sel.node === gate.id },
    reviewer: path
      ? { id: taskName(path), state: sessionLook(last, now).state, sel: sel.kind === "task" && sel.task === taskName(path), ...(verdict ? (verdict.type === "gate_approved" ? { chip: "approve", chipTone: "green" as const } : { chip: "reject", chipTone: "red" as const }) : ahead ? { chip: "runs first when reached" } : {}) }
      : undefined,
    message: own?.message ?? undefined,
    doc: pending && item.gate_artifact ? { label: item.gate_artifact.split("/").pop()!, onClick: on.doc } : undefined,
    reject: to ? { id: to, onClick: () => on.reject(to) } : undefined,
    youSub: pending ? "waiting" : decided ? decided.by : undefined,
  };
}
