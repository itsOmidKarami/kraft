import type { WorkItem } from "../../types";
import { gateConsequence } from "./finish";

/** The line under a gate review's header: what Approve does next. Nothing once the item is not waiting at the gate. */
export function GateConsequence({ item, gate, className }: { item: Pick<WorkItem, "pending_gate" | "chain_definition" | "budget_cap" | "running_time">; gate: string | null; className: string }) {
  const text = gateConsequence(item, gate);
  return text ? <p className={className}>{text}</p> : null;
}
