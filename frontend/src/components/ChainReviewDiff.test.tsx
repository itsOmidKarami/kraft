import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ChainReviewDiff } from "./ChainReviewDiff";
import type { WorkItem } from "../types/work_item";

const baseItem = {
  id: "w1",
  chain_definition: {
    template_id: "default",
    nodes: [
      { id: "chain_review", tasks: [], gate_after: "chain_finalized" },
      { id: "verify", tasks: ["on.test.run"], gate_after: null, on_failure: ["on.repair"] },
    ],
  },
} as unknown as WorkItem;

describe("ChainReviewDiff", () => {
  it("renders a diff for a real revision, with on_failure carried forward", () => {
    const content = JSON.stringify({
      status: "ready_for_approval",
      revised_chain_nodes: [{ id: "verify", tasks: ["on.test.run", "on.security.run"], gate_after: null }],
      rationale: "auth is in scope",
    });
    render(<ChainReviewDiff item={baseItem} content={content} />);
    expect(screen.getByTestId("draft-diff")).toBeInTheDocument();
    expect(screen.getByText("auth is in scope")).toBeInTheDocument();
  });

  it("renders flagged permission-surface concerns in their own panel", () => {
    const content = JSON.stringify({
      status: "ready_for_approval",
      revised_chain_nodes: [{ id: "verify", tasks: ["on.test.run"], gate_after: null }],
      flags: [{ hook_point: "on.test.run", field: "permission_mode", current_value: "bypassPermissions", concern: "wider than the plan needs" }],
      rationale: "t",
    });
    render(<ChainReviewDiff item={baseItem} content={content} />);
    expect(screen.getByText(/wider than the plan needs/)).toBeInTheDocument();
  });

  it("shows the rationale, not a diff, on status: error", () => {
    const content = JSON.stringify({ status: "error", rationale: "chain contradicts the plan" });
    render(<ChainReviewDiff item={baseItem} content={content} />);
    expect(screen.getByText("chain contradicts the plan")).toBeInTheDocument();
    expect(screen.queryByTestId("draft-diff")).not.toBeInTheDocument();
  });

  it("shows an error instead of crashing when revised_chain_nodes is not an array", () => {
    const content = JSON.stringify({
      status: "ready_for_approval",
      revised_chain_nodes: { verify: { tasks: [] } },
    });
    render(<ChainReviewDiff item={baseItem} content={content} />);
    expect(screen.getByText(/not the expected JSON envelope/)).toBeInTheDocument();
  });
});
