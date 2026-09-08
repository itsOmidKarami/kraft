import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { BudgetCard } from "./BudgetCard";
import type { WorkItem } from "../types";

const item = (budget: WorkItem["budget"]): WorkItem =>
  ({
    id: "w1",
    status: "needs_human",
    budget,
    chain_definition: {
      template_id: "t",
      nodes: [{ id: "n", tasks: [] as string[], gate_after: null }],
    },
    current_node_id: "n",
  }) as WorkItem;

beforeEach(() => vi.restoreAllMocks());

describe("BudgetCard", () => {
  it("shows the spend against the cap for a work-item breach", () => {
    render(<BudgetCard item={item({ scope: "work_item", spent_usd: 24.5, cap_usd: 20 })} />);
    expect(screen.getByTestId("budget-card")).toHaveTextContent("$24.50");
    expect(screen.getByTestId("budget-card")).toHaveTextContent("$20.00");
  });

  it("says a daily breach stops every item, not this one", () => {
    render(<BudgetCard item={item({ scope: "daily", spent_usd: 120, cap_usd: 100 })} />);
    expect(screen.getByTestId("budget-card")).toHaveTextContent(/every work item/i);
  });

  it("states that a running agent was not interrupted", () => {
    render(<BudgetCard item={item({ scope: "work_item", spent_usd: 24.5, cap_usd: 20 })} />);
    expect(screen.getByTestId("budget-card")).toHaveTextContent(/not interrupted/i);
  });

  it("tells the operator to raise the cap and then retry", () => {
    render(<BudgetCard item={item({ scope: "work_item", spent_usd: 24.5, cap_usd: 20 })} />);
    const card = screen.getByTestId("budget-card");
    expect(card).toHaveTextContent(/Settings → Policy, then retry/i);
    // Kraft-bzwi opened /retry on a loopless node, so this copy is now false
    expect(card).not.toHaveTextContent(/will not restart this item/i);
  });

  it("offers retry on a node with no fix loop, and warns it will re-breach", () => {
    // Kraft-bzwi removed the 409 that justified hiding this. A budget stop
    // leaves the item needs_human on a loopless node: resume wants paused,
    // pause wants running, approve/reject want a gate. Retry is the only door.
    render(<BudgetCard item={item({ scope: "work_item", spent_usd: 24.5, cap_usd: 20 })} />);
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    expect(screen.getByTestId("budget-card")).toHaveTextContent(
      /stops again at the next agent task/i,
    );
  });

  it("calls retryWorkItem with the item id when retried", async () => {
    const spy = vi.spyOn(api, "retryWorkItem").mockResolvedValue({
      id: "w1",
      node_id: "n",
      loop: "l",
      steer: null,
    });
    render(<BudgetCard item={item({ scope: "work_item", spent_usd: 24.5, cap_usd: 20 })} />);
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(spy).toHaveBeenCalledWith("w1");
  });
});
