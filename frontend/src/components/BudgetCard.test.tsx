import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { BudgetCard } from "./BudgetCard";
import type { WorkItem } from "../types";

const item = (budget: WorkItem["budget"], fixLoop = false): WorkItem =>
  ({
    id: "w1",
    status: "needs_human",
    budget,
    chain_definition: {
      template_id: "t",
      nodes: [{ id: "n", tasks: [] as string[], gate_after: null, fix_loop: fixLoop ? "l" : undefined }],
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

  it("offers no retry on a node with no fix loop, and no re-breach hint either", () => {
    render(<BudgetCard item={item({ scope: "work_item", spent_usd: 24.5, cap_usd: 20 })} />);
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
    expect(screen.getByTestId("budget-card")).not.toHaveTextContent(
      /stops again at the next agent task/i,
    );
  });

  it("does not tell a no-retry node that raising the cap will restart it", () => {
    // there is no control on this node at all: `/retry` 409s without a fix loop
    // and `/resume` only takes a paused item, so "raise the cap" on its own is
    // an instruction that does nothing.
    render(<BudgetCard item={item({ scope: "work_item", spent_usd: 24.5, cap_usd: 20 })} />);
    const card = screen.getByTestId("budget-card");
    expect(card).toHaveTextContent(/will not restart this item/i);
    expect(card).toHaveTextContent(/applies to the next item you start/i);
    expect(card).not.toHaveTextContent(/then retry/i);
  });

  it("tells a fix-loop node to raise the cap and then retry", () => {
    render(<BudgetCard item={item({ scope: "work_item", spent_usd: 24.5, cap_usd: 20 }, true)} />);
    const card = screen.getByTestId("budget-card");
    expect(card).toHaveTextContent(/Settings → Policy, then retry/i);
    expect(card).not.toHaveTextContent(/will not restart this item/i);
  });

  it("offers retry on a fix-loop node, and warns it will re-breach", () => {
    render(<BudgetCard item={item({ scope: "work_item", spent_usd: 24.5, cap_usd: 20 }, true)} />);
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    // the distinguishing words of the retry hint, not the sub text that also
    // mentions Settings → Policy in both branches
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
    render(<BudgetCard item={item({ scope: "work_item", spent_usd: 24.5, cap_usd: 20 }, true)} />);
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(spy).toHaveBeenCalledWith("w1");
  });
});
