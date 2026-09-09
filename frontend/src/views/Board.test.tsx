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

// selector pins this to the group heading, not the same-named Status facet button
const group = (label: string) =>
  screen.getByText(label, { selector: ".group-label" }).closest("section") as HTMLElement;

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
    await userEvent.click(screen.getByRole("button", { name: /^repo-a/ }));
    expect(screen.getAllByTestId("board-card")).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: /^repo-a/ }));
    expect(screen.getAllByTestId("board-card")).toHaveLength(2);
  });

  it("combines the repo and template facets, and counts each under the other", async () => {
    renderBoard();
    // With /repo-a picked, the template facet only counts that repo's items.
    await userEvent.click(screen.getByRole("button", { name: /^repo-a/ }));
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
    expect(within(row).getByText("approve the plan")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: /approve/i })).toBeInTheDocument();
  });

  it("does not offer a blind Approve for human_review_approval, only a link to the detail view", () => {
    setItems(
      wi({
        id: "w6",
        status: "needs_human",
        current_node_id: "verify",
        pending_gate: "human_review_approval",
      }),
    );
    renderBoard();
    const row = within(group("Needs you")).getByTestId("board-card");
    expect(within(row).queryByRole("button", { name: /approve/i })).toBeNull();
    const link = within(row).getByRole("link", { name: /review to approve/i });
    expect(link).toHaveAttribute("href", "/work-items/w6");
  });

  it("spells out the cap on a capped-out row", () => {
    setItems(
      wi({ id: "w4", status: "needs_human", cappedOut: { cycles: 3, attempts: 3 } }),
    );
    renderBoard();
    expect(screen.getByText(/capped 3\/3/)).toBeInTheDocument();
  });

  it("puts a paused-mid-chain item in Needs you, and a never-started item in Not started", () => {
    setItems(
      wi({ id: "w5", status: "paused", current_node_id: "verify", title: "Paused mid-chain" }),
      wi({ id: "w7", status: "paused", current_node_id: null, title: "Never started" }),
    );
    renderBoard();
    expect(within(group("Needs you")).getByText("Paused mid-chain")).toBeInTheDocument();
    expect(within(group("Not started")).getByText("Never started")).toBeInTheDocument();
    expect(within(group("Needs you")).queryByText("Never started")).not.toBeInTheDocument();
    expect(within(group("Running")).queryAllByTestId("board-card")).toHaveLength(0);
  });

  it("filters on the status facet", async () => {
    setItems(
      wi({ id: "w1", status: "active" }),
      wi({ id: "w7", status: "paused", current_node_id: null, title: "Never started" }),
    );
    renderBoard();
    expect(screen.getAllByTestId("board-card")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: /^Not started/ }));
    expect(screen.getAllByTestId("board-card")).toHaveLength(1);
    expect(screen.getByText("Never started")).toBeInTheDocument();
  });

  it("sorts within a group by the chosen key", async () => {
    setItems(
      wi({ id: "w1", status: "active", title: "Bravo", updated_at: "2024-01-01T00:00:00Z" }),
      wi({ id: "w2", status: "active", title: "Alfa", updated_at: "2024-06-01T00:00:00Z" }),
    );
    renderBoard();
    // default: recently updated first
    let rows = within(group("Running")).getAllByTestId("board-card");
    expect(within(rows[0]).getByText("Alfa")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText(/sort/i), "title");
    rows = within(group("Running")).getAllByTestId("board-card");
    expect(within(rows[0]).getByText("Alfa")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Bravo")).toBeInTheDocument();
  });

  it("marks an item that started from existing documents", () => {
    setItems(
      wi({
        id: "w1",
        attachments: [{ kind: "plan", path: ".engineering/plans/p.md" }],
      }),
    );
    renderBoard();
    expect(screen.getByText("from plan")).toBeInTheDocument();
  });
});
