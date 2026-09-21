import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MiniChain } from "../../../components/ui";
import type { ChainNode, TemplateSummary, WorkItem } from "../../../types";
import { templatesUsingLoop } from "../../settings/PolicyPage";
import { Config } from "../Inspector/Config";
import { ChainDescription, NotStartedCard, gateNote } from "../NotStarted";
import { yamlOf } from "../RightPane/ConfigPane";
import { gateNodeId, rejectTarget } from "./ItemCard";

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
    title: "T",
    repo: "/r",
    status: "paused",
    chain_template: "default",
    chain_definition: { template_id: "default", nodes: V1_NODES },
    effective_chain: { template_id: "default", nodes: V1_NODES },
    current_node_id: "spec_approval",
    created_at: "t",
    updated_at: "t",
  }) as WorkItem;

/** The three questions the SPA asks of `gate_after`, each in the shape its own
 *  consumer asks it. Before the projection filled the field for a V1 gate all
 *  three answered "this chain has no gates" -- `gateNodeId` returned null, so
 *  the gate's document door disappeared, and the stage bar and gate counts read
 *  zero. Silently wrong is worse than the blank board it replaced. */
describe("a V1 chain answers the gate questions the same way a legacy one does", () => {
  it('"is this node a gate" -- the stage bar\'s flag', () => {
    render(
      <MiniChain nodes={V1_NODES} currentNodeId="spec_approval" size="lg" />,
    );
    expect(
      screen.getByTestId("node-spec_approval").querySelector(".chain-gate"),
    ).not.toBeNull();
    expect(
      screen.getByTestId("node-spec").querySelector(".chain-gate"),
    ).toBeNull();
  });

  it('"how many gates" -- the not-started chain description', () => {
    render(<ChainDescription item={v1Item()} />);
    expect(screen.getByText(/3 nodes, 1 gate/)).toBeInTheDocument();
  });

  it('"what is the first gate called" -- NotStarted\'s intake card', () => {
    render(<NotStartedCard item={v1Item()} />);
    expect(screen.getByTestId("not-started-card")).toHaveTextContent(
      "first gatespec_approval",
    );
  });

  it('"which node owns gate X" -- ItemCard.gateNodeId, the gate\'s document door', () => {
    expect(gateNodeId(v1Item(), "spec_approval")).toBe("spec_approval");
    expect(gateNodeId(v1Item(), "nope")).toBeNull();
  });

  it("rejectTarget resolves a V1 gate's reject_to to a node earlier in the chain", () => {
    expect(rejectTarget(v1Item(), "spec_approval")).toBe("spec");
  });

  it("PolicyPage's reject-loop key matches the gate node's own id", () => {
    const templates = [
      { id: "v1", nodes: V1_NODES },
      { id: "other", nodes: [V1_NODES[0]] },
    ] as unknown as TemplateSummary[];
    expect(templatesUsingLoop(templates, "spec_approval_reject_loop")).toEqual([
      "v1",
    ]);
  });

  it("Inspector/Config's auto-escalate toggle is enabled on a V1 gate", () => {
    // `disabled={... || !node.gate_after}` -- null there disabled the only
    // control that arms agent gate review. `current_node_id` is the node
    // *before* the gate, or `started` would lock it for an unrelated reason.
    render(
      <Config
        item={{ ...v1Item(), current_node_id: "spec" } as WorkItem}
        nodeId="spec_approval"
      />,
    );
    expect(screen.getByRole("switch")).not.toBeDisabled();
  });
});

/** A V1 gate node reporting itself under `gate_after` is what makes the eleven
 *  gate-question consumers correct -- and it is also a shape no authored
 *  template can have, so the two places that *render* the field have to say
 *  something else. Confusing-but-correct is still confusing. */
describe("a V1 gate node does not render as pointing at itself", () => {
  it("the not-started chain list says `gate`, not `then gate <its own id>`", () => {
    const gate = V1_NODES.find((n) => n.id === "spec_approval")!;
    const exec = V1_NODES.find((n) => n.id === "spec")!;
    expect(gateNote(gate)).toBe(" · gate");
    expect(gateNote(gate)).not.toContain("spec_approval");
    // And a legacy exec node still names the gate it leads to.
    expect(gateNote({ ...exec, gate_after: "spec_approval" })).toBe(
      " · then gate spec_approval",
    );
  });

  it("the config YAML prints the field a V1 gate actually declares", () => {
    const item = {
      ...v1Item(),
      chain_definition: { template_id: "default", nodes: V1_NODES },
    } as WorkItem;
    const text = yamlOf(item, null).map((l) => l.text);
    expect(text).toContain("    kind: gate, reject_to: spec");
    // The gate's own entry does not claim to lead to itself.
    expect(text).not.toContain("    gate_after: spec_approval");
    // An exec node keeps the field it really has.
    expect(text).toContain("    gate_after: null");
  });
});
