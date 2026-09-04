import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
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
  vi.spyOn(api, "getWorkItemDocuments").mockResolvedValue({
    work_item_id: "w1",
    documents: [],
  });
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

  it("shows the linked documents panel above the event timeline", async () => {
    const { container } = render(
      <MemoryRouter
        initialEntries={["/work-items/w1"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/work-items/:id" element={<WorkItemDetail />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/no linked documents/i)).toBeInTheDocument();
    expect(api.getWorkItemDocuments).toHaveBeenCalledWith("w1");
    const docs = container.querySelector(".linked-docs");
    const timeline = container.querySelector(".timeline");
    expect(docs).not.toBeNull();
    expect(timeline).not.toBeNull();
    expect(
      docs!.compareDocumentPosition(timeline!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
