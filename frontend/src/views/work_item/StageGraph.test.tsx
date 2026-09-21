import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { ChainNode, WorkItem } from "../../types";
import { StageGraph } from "./StageGraph";
import { item as baseItem } from "../../testFixtures";

vi.mock("../../api");

const NODES: ChainNode[] = [
  { id: "plan", tasks: ["on.plan.requested"], gate_after: "plan_approval" },
  { id: "implement", tasks: ["on.implementation.start"], gate_after: null },
];

const item = (over: Partial<WorkItem> = {}): WorkItem =>
  baseItem({ id: "wi_01HX3K9", chain_definition: { template_id: "default", nodes: NODES }, current_node_id: "implement", completedNodes: ["plan"], ...over });

describe("StageGraph (W0.6: gate mark)", () => {
  it("flags the current pill whenever pending_gate is set, auto_escalate or not", () => {
    vi.mocked(api.getTemplate).mockRejectedValue(new Error("nope"));
    render(<StageGraph item={item({ pending_gate: "plan_approval", status: "needs_human" })} selected={null} onSelect={() => {}} />);
    const current = document.querySelector(".stage-pill[aria-current='step']") as HTMLElement;
    expect(current.dataset.gate).toBe("true");
    expect(current.querySelector(".stage-pill-gate")).toBeTruthy();
    expect(current.querySelector(".stage-pill-escalate")).toBeNull();
    expect(document.querySelectorAll(".stage-pill-gate")).toHaveLength(1);
  });

  it("draws no gate mark when nothing is pending", () => {
    vi.mocked(api.getTemplate).mockRejectedValue(new Error("nope"));
    render(<StageGraph item={item()} selected={null} onSelect={() => {}} />);
    expect(document.querySelector(".stage-pill-gate")).toBeNull();
  });
});

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

describe("StageGraph · vocabulary (Kraft-snz7s)", () => {
  it("labels the nav 'chain nodes', the settled word", () => {
    render(<StageGraph item={item()} selected={null} onSelect={() => {}} />);
    expect(document.querySelector('nav[aria-label="chain nodes"]')).toBeTruthy();
    expect(document.querySelector('nav[aria-label="chain stages"]')).toBeNull();
  });
});
