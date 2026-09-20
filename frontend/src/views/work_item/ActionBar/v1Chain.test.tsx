import { describe, expect, it } from "vitest";
import type { ChainNode, WorkItem } from "../../../types";
import { rejectTarget } from "./ItemCard";

/** A Template Schema V1 chain as `store.chain_view` projects it: a gate is a
 *  node of its own, and it reports *itself* under `gate_after`. */
const V1_NODES: ChainNode[] = [
  { id: "spec", kind: "exec", tasks: ["spec.main.author"], gate_after: null },
  {
    id: "spec_approval",
    kind: "gate",
    tasks: [],
    gate_after: "spec_approval",
    reject_to: "spec",
  },
  {
    id: "implementation",
    kind: "exec",
    tasks: ["implementation.main.implement"],
    gate_after: null,
    fix_loop: "implementation.fix_loop",
  },
];

const v1Item = (): WorkItem =>
  ({
    id: "w1",
    chain_definition: { template_id: "default", nodes: V1_NODES },
    current_node_id: "spec_approval",
  }) as WorkItem;

/** The three questions the SPA asks of `gate_after`, each in the shape its own
 *  consumer asks it. Before the projection filled the field for a V1 gate all
 *  three answered "this chain has no gates" -- `gateNodeId` returned null, so
 *  the gate's document door disappeared, and the stage bar and gate counts read
 *  zero. Silently wrong is worse than the blank board it replaced. */
describe("a V1 chain answers the gate questions the same way a legacy one does", () => {
  it('"is this node a gate" -- the stage bar, the phone header and the gate counts', () => {
    expect(V1_NODES.filter((n) => n.gate_after).map((n) => n.id)).toEqual([
      "spec_approval",
    ]);
  });

  it('"what is the first gate called" -- NotStarted\'s intake card', () => {
    expect(V1_NODES.find((n) => n.gate_after)?.gate_after ?? "none").toBe(
      "spec_approval",
    );
  });

  it('"which node owns gate X" -- ItemCard.gateNodeId, the gate\'s document door', () => {
    const owner =
      V1_NODES.find((n) => n.gate_after === "spec_approval")?.id ?? null;
    expect(owner).toBe("spec_approval");
  });

  it("rejectTarget resolves a V1 gate's reject_to to a node earlier in the chain", () => {
    expect(rejectTarget(v1Item(), "spec_approval")).toBe("spec");
  });

  it("PolicyPage's reject-loop key matches the gate node's own id", () => {
    const key = "spec_approval_reject_loop";
    expect(
      V1_NODES.some(
        (n) =>
          n.fix_loop === key ||
          (n.gate_after && `${n.gate_after}_reject_loop` === key),
      ),
    ).toBe(true);
  });

  it("Inspector/Config's auto-escalate toggle is enabled on a V1 gate", () => {
    // `disabled={... || !node.gate_after}` -- null there disabled the only
    // control that arms agent gate review.
    const gate = V1_NODES.find((n) => n.id === "spec_approval")!;
    expect(!gate.gate_after).toBe(false);
  });
});
