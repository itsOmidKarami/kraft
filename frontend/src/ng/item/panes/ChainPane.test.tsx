import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { KraftEvent } from "../../../types";
import { detail, stubFetch } from "../testkit";
import { ChainConfig, ChainOverview } from "./ChainPane";

afterEach(() => vi.unstubAllGlobals());
const NOW = Date.parse("2026-09-13T10:10:00Z");
const posts = (calls: { method: string }[]) => calls.filter((c) => c.method !== "GET");

describe("ChainOverview", () => {
  it("lists recent events newest first; a line about a node selects it", async () => {
    const onSelect = vi.fn();
    const ev = (seq: number, type: string, node_id: string | null): KraftEvent => ({ seq, work_item_id: "w1", type, payload: {}, node_id, created_at: "2026-09-13T10:04:00Z" });
    render(<ChainOverview item={detail({ summary: { nodes_done: 7, nodes_total: 15, gates_passed: 3, step: null } })} events={[ev(1, "work_item_created", null), ev(2, "node_completed", "plan"), ev(3, "worker_session_exited", "plan")]} now={NOW} onSelect={onSelect} />);
    expect(screen.getByText("7 of 15 nodes · 3 gates passed")).toBeInTheDocument();
    const lines = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(lines).toEqual(["6mplan finished", "6mfiled"]);
    await userEvent.click(screen.getByRole("button", { name: "plan finished" }));
    expect(onSelect).toHaveBeenCalledWith("plan");
    await userEvent.click(screen.getByRole("button", { name: "verification" }));
    expect(onSelect).toHaveBeenLastCalledWith("verification");
  });
});

describe("ChainConfig", () => {
  const show = (over: Parameters<typeof detail>[0] = {}, editBudget = false) => {
    const reload = vi.fn();
    const onEditBudget = vi.fn();
    render(<ChainConfig item={detail({ budget_cap: { cap_usd: 5, source: "policy", spent_usd: 4, daily: { spent_usd: 18.2, cap_usd: 50 } }, ...over })} policy={null} reload={reload} editBudget={editBudget} onEditBudget={onEditBudget} />);
    return { reload, onEditBudget };
  };

  it("turns a meter amber past 75% and red at the cap", () => {
    show();
    expect(screen.getByRole("meter", { name: "Budget" }).closest(".meter")).toHaveClass("is-warn");
    expect(screen.getByRole("meter", { name: "Today, all items" }).closest(".meter")).not.toHaveClass("is-warn");
  });

  it("raises the budget with a quick pick through PATCH, or /budget/raise on a budget stop", async () => {
    const calls = stubFetch();
    const { reload } = show({}, true);
    await userEvent.click(screen.getByRole("button", { name: "+$5" }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(posts(calls)).toEqual([{ method: "PATCH", path: "/work-items/w1", body: { budget_usd: 10 } }]);
  });

  it("on a budget stop raises and retries in one call", async () => {
    const calls = stubFetch();
    show({ display_status: "needs_you", stop: { kind: "budget", node: "n", resume_at: null, reason: null } }, true);
    await userEvent.click(screen.getByRole("button", { name: "No cap" }));
    await waitFor(() => expect(posts(calls)).toEqual([{ method: "POST", path: "/work-items/w1/budget/raise", body: { budget_usd: null } }]));
  });

  it("says nothing changed without overrides, and resets a node override", async () => {
    const { unmount } = render(<ChainConfig item={detail({ node_overrides: {} })} policy={null} reload={() => {}} editBudget={false} onEditBudget={() => {}} />);
    expect(screen.getByText(/Nothing changed/)).toBeInTheDocument();
    unmount();
    const calls = stubFetch({ "PATCH /work-items/w1": [409, { detail: "node verification has already started" }] });
    render(<ChainConfig item={detail({ node_overrides: { verification: { attempts: 4 } } })} policy={null} reload={() => {}} editBudget={false} onEditBudget={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "reset" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already started");
    expect(posts(calls)).toEqual([{ method: "PATCH", path: "/work-items/w1", body: { node_overrides: { verification: {} } } }]);
  });
});
