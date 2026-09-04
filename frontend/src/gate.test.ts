import { describe, expect, it } from "vitest";
import { awaitingGate } from "./gate";
import type { WorkItem, WorkerSession } from "./types";

const item = (over: Partial<WorkItem> = {}): WorkItem =>
  ({
    id: "w1",
    current_node_id: "verify",
    chain_definition: {
      template_id: "t",
      nodes: [
        { id: "verify", tasks: ["on.test.run"], gate_after: "human_review_approval" },
        { id: "ship", tasks: ["on.ship"], gate_after: null },
      ],
    },
    ...over,
  }) as WorkItem;

const s = (over: Partial<WorkerSession>): WorkerSession =>
  ({
    id: "s1", work_item_id: "w1", node_id: "verify", hook_point: "on.test.run",
    status: "done", attempt: 1, created_at: "t", exited_at: null, ...over,
  }) as WorkerSession;

describe("awaitingGate", () => {
  it("fires once the gated node's sessions have all ended clean", () => {
    expect(awaitingGate(item(), [s({})])).toBe("human_review_approval");
  });

  it("holds off while a session on the node is still running", () => {
    expect(awaitingGate(item(), [s({ status: "running" })])).toBeNull();
  });

  it("holds off when the node has started no sessions at all", () => {
    expect(awaitingGate(item(), [])).toBeNull();
  });

  it("holds off once the next node has started — the gate is already past", () => {
    expect(awaitingGate(item(), [s({}), s({ id: "s2", node_id: "ship" })])).toBeNull();
  });

  it("is null on an ungated node", () => {
    const ungated = item();
    ungated.chain_definition.nodes[0].gate_after = null;
    expect(awaitingGate(ungated, [s({})])).toBeNull();
  });
});
