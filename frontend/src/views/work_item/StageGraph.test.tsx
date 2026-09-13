import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { ChainNode, WorkItem } from "../../types";
import { StageGraph } from "./StageGraph";

vi.mock("../../api");

const NODES: ChainNode[] = [
  { id: "plan", tasks: ["on.plan.requested"], gate_after: "plan_approval" },
  { id: "implement", tasks: ["on.implementation.start"], gate_after: null },
];

const item = (over: Partial<WorkItem> = {}): WorkItem =>
  ({
    id: "wi_01HX3K9", title: "T", repo: "/r", status: "active", chain_template: "default",
    chain_definition: { template_id: "default", nodes: NODES },
    current_node_id: "implement", bead_id: "B", created_at: "t", updated_at: "t",
    completedNodes: ["plan"],
    ...over,
  }) as WorkItem;

describe("StageGraph (Kraft-1brd: trimmed-node placeholders)", () => {
  it("renders only the live nodes while the template fetch is pending or fails", async () => {
    vi.mocked(api.getTemplate).mockRejectedValue(new Error("nope"));
    render(<StageGraph item={item()} selected={null} onSelect={() => {}} />);
    expect(screen.getAllByRole("button")).toHaveLength(2);
    await waitFor(() => expect(api.getTemplate).toHaveBeenCalledWith("default"));
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });

  it("shows a dimmed placeholder pill for a node the template lists but the chain trimmed", async () => {
    vi.mocked(api.getTemplate).mockResolvedValue({
      id: "default",
      nodes: [
        { id: "spec", tasks: [], gate_after: "spec_approval" },
        { id: "plan", tasks: [], gate_after: "plan_approval" },
        { id: "implement", tasks: [], gate_after: null },
      ],
    });
    render(<StageGraph item={item()} selected={null} onSelect={() => {}} />);
    await waitFor(() => expect(screen.getByText("–")).toBeInTheDocument());
    const placeholder = screen.getByText("–");
    expect(placeholder.tagName).toBe("SPAN");
    expect(placeholder).toHaveAttribute("data-state", "trimmed");
    // still just the two live nodes as clickable pills
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });

  it("falls back to the live list when the template no longer accounts for a live node", async () => {
    vi.mocked(api.getTemplate).mockResolvedValue({
      id: "default",
      nodes: [{ id: "implement", tasks: [], gate_after: null }],
    });
    render(<StageGraph item={item()} selected={null} onSelect={() => {}} />);
    await waitFor(() => expect(api.getTemplate).toHaveBeenCalled());
    expect(screen.queryByText("–")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });
});
