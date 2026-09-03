import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { WorkItem, WorkerSession } from "../types";
import { CurrentNodePanel } from "./CurrentNodePanel";

const item = {
  id: "w1",
  current_node_id: "verify",
  fixCycle: 2,
  chain_definition: {
    template_id: "t",
    nodes: [
      { id: "verify", tasks: ["on.test.run"], gate_after: null, fix_loop: "verify_fix_loop" },
    ],
  },
} as WorkItem;

const s = (over: Partial<WorkerSession>): WorkerSession =>
  ({
    id: "s1", work_item_id: "w1", node_id: "verify", hook_point: "on.test.run",
    status: "running", attempt: 1, created_at: "t", exited_at: null, ...over,
  }) as WorkerSession;

describe("CurrentNodePanel", () => {
  it("renders a chip per current-node session", () => {
    render(<CurrentNodePanel item={item} sessions={[s({ id: "a", status: "running" }), s({ id: "b", status: "capped_out" })]} />);
    expect(screen.getByTestId("session-a")).toHaveAttribute("data-status", "running");
    expect(screen.getByTestId("session-b")).toHaveAttribute("data-status", "capped_out");
  });

  it("badges a fix task row", () => {
    render(<CurrentNodePanel item={item} sessions={[s({ id: "f", hook_point: "on.implementation.start" })]} />);
    expect(screen.getByTestId("session-f")).toHaveTextContent("fix · cycle 2");
  });

  const gated = {
    id: "w1",
    current_node_id: "verify",
    chain_definition: {
      template_id: "t",
      nodes: [
        { id: "verify", tasks: ["on.test.run"], gate_after: "human_review_approval" },
        { id: "ship", tasks: ["on.ship"], gate_after: null },
      ],
    },
  } as WorkItem;

  it("renders <Gate> when the current node has a gate and all its sessions are done", () => {
    render(<CurrentNodePanel item={gated} sessions={[s({ id: "a", status: "done" })]} />);
    expect(screen.getByText(/human_review_approval/)).toBeInTheDocument();
  });

  it("does NOT render <Gate> while a current-node session is still running", () => {
    render(<CurrentNodePanel item={gated} sessions={[s({ id: "a", status: "running" })]} />);
    expect(screen.queryByText(/human_review_approval/)).not.toBeInTheDocument();
  });
});
