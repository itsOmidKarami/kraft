import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import type { WorkItem } from "../types";
import { Board } from "./Board";

const wi = (over: Partial<WorkItem>): WorkItem =>
  ({
    id: over.id ?? "w1",
    title: over.title ?? "Item",
    repo: over.repo ?? "/repo-a",
    status: over.status ?? "active",
    chain_template: "quick-task",
    chain_definition: { template_id: "quick-task", nodes: [{ id: "n", tasks: ["a"], gate_after: null }] },
    current_node_id: "n",
    bead_id: "B",
    created_at: "t",
    updated_at: "t",
    ...over,
  }) as WorkItem;

beforeEach(() => {
  useStore.setState({
    workItems: {
      w1: wi({ id: "w1", repo: "/repo-a", status: "active" }),
      w2: wi({ id: "w2", repo: "/repo-b", status: "completed" }),
    },
  } as never);
  vi.restoreAllMocks();
  // Board re-bootstraps on mount; keep it inert so tests keep the state set above.
  vi.spyOn(useStore.getState(), "bootstrap").mockResolvedValue();
});

const renderBoard = () =>
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Board />
    </MemoryRouter>,
  );

describe("Board", () => {
  it("renders a card per work item", () => {
    renderBoard();
    expect(screen.getAllByTestId("board-card")).toHaveLength(2);
  });

  it("filters by status", async () => {
    renderBoard();
    await userEvent.selectOptions(screen.getByLabelText("status"), "completed");
    const cards = screen.getAllByTestId("board-card");
    expect(cards).toHaveLength(1);
    expect(within(cards[0]).getByText("w2", { exact: false })).toBeTruthy();
  });

  it("opens the intake modal", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task" }, { id: "default" }]);
    renderBoard();
    await userEvent.click(screen.getByRole("button", { name: /new work item/i }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});
