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
});
