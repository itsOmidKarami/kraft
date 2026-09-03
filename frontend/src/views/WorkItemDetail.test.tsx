import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import { WorkItemDetail } from "./WorkItemDetail";

beforeEach(() => {
  useStore.setState({
    workItems: {
      w1: {
        id: "w1", title: "T", repo: "/r", status: "active", chain_template: "quick-task",
        chain_definition: { template_id: "quick-task", nodes: [{ id: "n", tasks: ["a"], gate_after: null }] },
        current_node_id: "n", bead_id: "B", created_at: "t", updated_at: "t",
      },
    },
    sessionsByItem: { w1: [] },
    eventsByItem: { w1: [] },
  } as never);
  vi.spyOn(useStore.getState(), "hydrateItem").mockResolvedValue(undefined);
});

describe("WorkItemDetail", () => {
  it("hydrates on mount and renders the stepper", async () => {
    render(
      <MemoryRouter
        initialEntries={["/work-items/w1"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/work-items/:id" element={<WorkItemDetail />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(useStore.getState().hydrateItem).toHaveBeenCalledWith("w1");
    expect(screen.getByTestId("node-n")).toBeInTheDocument();
  });
});
