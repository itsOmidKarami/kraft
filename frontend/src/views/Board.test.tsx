import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import type { WorkItem } from "../types";
import { Board } from "./Board";

const wi = (over: Partial<WorkItem>): WorkItem =>
  ({
    id: over.id ?? "w1",
    title: over.title ?? "Item",
    repo: over.repo ?? "/repo-a",
    status: over.status ?? "active",
    chain_template: over.chain_template ?? "quick-task",
    chain_definition: {
      template_id: "quick-task",
      nodes: [
        { id: "plan", tasks: ["a"], gate_after: "plan_approval" },
        { id: "verify", tasks: ["b"], gate_after: null },
      ],
    },
    current_node_id: "verify",
    bead_id: "B",
    created_at: "t",
    updated_at: "t",
    ...over,
  }) as WorkItem;

const setItems = (...items: WorkItem[]) =>
  useStore.setState({ workItems: Object.fromEntries(items.map((i) => [i.id, i])) } as never);

beforeEach(() => {
  setItems(
    wi({ id: "w1", repo: "/repo-a", status: "active" }),
    wi({ id: "w2", repo: "/repo-b", status: "completed", chain_template: "default" }),
  );
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

const group = (label: string) =>
  screen.getByText(label).closest("section") as HTMLElement;

describe("Board", () => {
  it("groups by attention rather than showing a status column", () => {
    renderBoard();
    expect(within(group("Running")).getAllByTestId("board-card")).toHaveLength(1);
    expect(within(group("Done")).getAllByTestId("board-card")).toHaveLength(1);
    expect(within(group("Needs you")).queryAllByTestId("board-card")).toHaveLength(0);
    expect(within(group("Needs you")).getByText(/nothing here/)).toBeInTheDocument();
  });

  it("filters on the repo facet and clears it when the same facet is clicked again", async () => {
    renderBoard();
    await userEvent.click(screen.getByRole("button", { name: /\/repo-a/ }));
    expect(screen.getAllByTestId("board-card")).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: /\/repo-a/ }));
    expect(screen.getAllByTestId("board-card")).toHaveLength(2);
  });

  it("combines the repo and template facets, and counts each under the other", async () => {
    renderBoard();
    // With /repo-a picked, the template facet only counts that repo's items.
    await userEvent.click(screen.getByRole("button", { name: /\/repo-a/ }));
    expect(screen.getByRole("button", { name: /quick-task/ })).toHaveTextContent("1");
    expect(screen.queryByRole("button", { name: /default/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /quick-task/ }));
    expect(screen.getAllByTestId("board-card")).toHaveLength(1);
  });

  it("caps the Done group at five until 'show all' is clicked", async () => {
    setItems(
      ...Array.from({ length: 7 }, (_, n) =>
        wi({ id: `d${n}`, status: "completed", title: `Done ${n}` }),
      ),
    );
    renderBoard();
    expect(within(group("Done")).getAllByTestId("board-card")).toHaveLength(5);
    await userEvent.click(screen.getByRole("button", { name: /show all 7/ }));
    expect(within(group("Done")).getAllByTestId("board-card")).toHaveLength(7);
  });

  it("offers the gate inline on a needs-you row, naming the current node's gate", () => {
    setItems(
      wi({ id: "w3", status: "needs_human", current_node_id: "plan", pending_gate: "plan_approval" }),
    );
    renderBoard();
    const row = within(group("Needs you")).getByTestId("board-card");
    expect(within(row).getByText("plan_approval")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: /approve/i })).toBeInTheDocument();
  });

  it("spells out the cap on a capped-out row", () => {
    setItems(
      wi({ id: "w4", status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } }),
    );
    renderBoard();
    expect(screen.getByText(/capped 3\/3/)).toBeInTheDocument();
  });

  it("keeps a paused item on the board, in the group that is waiting on you", () => {
    setItems(wi({ id: "w5", status: "paused", title: "Paused item" }));
    renderBoard();
    expect(within(group("Needs you")).getByText("Paused item")).toBeInTheDocument();
    expect(within(group("Running")).queryAllByTestId("board-card")).toHaveLength(0);
  });
});
