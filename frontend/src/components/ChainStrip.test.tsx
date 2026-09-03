import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ChainStrip } from "./ChainStrip";
import type { WorkItem } from "../types";

const item = (over: Partial<WorkItem> = {}): WorkItem =>
  ({
    id: "w1",
    current_node_id: "verify",
    completedNodes: ["env_setup"],
    chain_definition: {
      template_id: "t",
      nodes: [
        { id: "env_setup", tasks: ["a"], gate_after: null },
        { id: "verify", tasks: ["a", "b"], gate_after: null, fix_loop: "verify_fix_loop" },
        { id: "done", tasks: ["c"], gate_after: "human_review_approval" },
      ],
    },
    ...over,
  } as WorkItem & typeof over);

describe("ChainStrip", () => {
  it("marks current, done, gates, and the task-count badge", () => {
    render(<ChainStrip item={item()} size="sm" />);
    expect(screen.getByTestId("node-env_setup").className).toContain("done");
    expect(screen.getByTestId("node-verify").className).toContain("current");
    expect(screen.getByTestId("node-verify")).toHaveTextContent("2"); // task-count badge
    expect(screen.getByTitle("human_review_approval")).toBeInTheDocument(); // gate marker
  });

  it("shows the fix-cycle badge on the current fix_loop node", () => {
    render(<ChainStrip item={item({ fixCycle: 3 })} size="lg" />);
    expect(screen.getByTestId("node-verify")).toHaveTextContent("fix · 3");
  });
});
