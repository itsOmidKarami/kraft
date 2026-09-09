import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import type { Analytics, WorkItem } from "../types";
import { AnalyticsView } from "./Analytics";

const report: Analytics = {
  totals: {
    work_items: 9,
    work_items_run: 6,
    by_status: { completed: 6, needs_human: 3 },
    mrs_merged: 4,
    wall_ms: 3 * 3_600_000,
    human_wait_ms: 12 * 3_600_000,
    tokens_in: 1_200_000,
    tokens_out: 200_000,
    cost_usd: 18.0, cost_complete: true,
    rounds: 5,
    capped_out: 2,
  },
  weekly_merged: [{ week_start: "2026-08-31", n: 4 }],
  by_node: [
    { node: "implementation", runs: 9, wall_ms: 900_000, avg_ms: 100_000, tokens: 1_000_000, cost_usd: 15, cost_complete: true, rounds: 5, capped_out: 2 },
    { node: "verify", runs: 12, wall_ms: 120_000, avg_ms: 10_000, tokens: 400_000, cost_usd: 3, cost_complete: true, rounds: 5, capped_out: 0 },
  ],
  by_repo: [
    { repo: "/repo-a", items: 9, mrs: 4, tokens: 1_400_000, cost_usd: 18, cost_complete: true },
  ],
};

const wi = (over: Partial<WorkItem>): WorkItem =>
  ({ id: "w1", repo: "/repo-a", chain_template: "default", ...over }) as WorkItem;

beforeEach(() => {
  useStore.setState({ workItems: { w1: wi({}), w2: wi({ id: "w2", repo: "/repo-b" }) } } as never);
  vi.restoreAllMocks();
  vi.spyOn(useStore.getState(), "bootstrap").mockResolvedValue();
  vi.spyOn(api, "getAnalytics").mockResolvedValue(report);
});

const renderView = () =>
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AnalyticsView />
    </MemoryRouter>,
  );

describe("AnalyticsView", () => {
  it("asks for the last 30 days by default and says so", async () => {
    renderView();
    await screen.findByText("Analytics");
    await screen.findAllByText("$18.00");
    expect(api.getAnalytics).toHaveBeenCalledWith({
      range: "30d",
      repo: undefined,
      template: undefined,
    });
    expect(screen.getByText(/last 30 days · all repos · all templates/)).toBeInTheDocument();
  });

  it("shows six KPIs, each with a sub-stat", async () => {
    const { container } = renderView();
    await screen.findAllByText("$18.00");
    expect(container.querySelectorAll(".kpi")).toHaveLength(6);
    expect(screen.getByText("6 completed · 3 needs human")).toBeInTheDocument();
    expect(screen.getByText("+ 12h waiting on people")).toBeInTheDocument();
    expect(screen.getByText("1.2M in · 200k out")).toBeInTheDocument();
    // $18.00 over the 6 items that ran, not over all 9 — 9 is the backlog
    expect(screen.getByText("$3.00 per work item run")).toBeInTheDocument();
    expect(screen.getByText("2 sessions capped out")).toBeInTheDocument();
  });

  it("draws seven week columns even though only one week merged anything", async () => {
    const { container } = renderView();
    await screen.findAllByText("$18.00");
    expect(container.querySelectorAll(".bar-col")).toHaveLength(7);
    // the newest column is the partial one
    const cols = [...container.querySelectorAll(".bar-col")];
    expect(cols[6]).toHaveAttribute("data-partial", "true");
    expect(cols.filter((c) => c.querySelector(".bar-n")?.textContent !== "0")).toHaveLength(1);
  });

  it("ranks nodes by cost share and repos by items", async () => {
    const { container } = renderView();
    await screen.findAllByText("$18.00");
    const nodes = [...container.querySelectorAll(".node-row[data-node]")];
    expect(nodes.map((n) => n.getAttribute("data-node"))).toEqual(["implementation", "verify"]);
    // the top node's bar is full width; the cheaper one is proportional
    expect((nodes[0].querySelector(".share > span") as HTMLElement).style.width).toBe("100%");
    expect((nodes[1].querySelector(".share > span") as HTMLElement).style.width).toBe("20%");
    expect(within(nodes[0] as HTMLElement).getByText("1M")).toBeInTheDocument();
  });

  it("refetches when a filter changes", async () => {
    renderView();
    await screen.findAllByText("$18.00");
    await userEvent.click(screen.getByRole("button", { name: "Last 7 days" }));
    expect(api.getAnalytics).toHaveBeenLastCalledWith({
      range: "7d",
      repo: undefined,
      template: undefined,
    });
    // the facet reads as the repo's own name; the filter still sends the path
    await userEvent.click(screen.getByRole("button", { name: "repo-b" }));
    expect(api.getAnalytics).toHaveBeenLastCalledWith({
      range: "7d",
      repo: "/repo-b",
      template: undefined,
    });
  });

  it("surfaces a failed fetch instead of an empty page", async () => {
    vi.spyOn(api, "getAnalytics").mockRejectedValue(new Error("unknown range"));
    renderView();
    expect(await screen.findByText(/unknown range/)).toHaveClass("form-error");
  });

  it("marks a cost that is only a floor, and never dresses it up as a total", async () => {
    vi.spyOn(api, "getAnalytics").mockResolvedValue({
      ...report,
      totals: { ...report.totals, cost_complete: false },
      by_node: report.by_node.map((n) => ({ ...n, cost_complete: false })),
    });
    renderView();
    await screen.findAllByText("$18.00+");
    expect(screen.getByText("a floor — some runs reported no cost")).toBeInTheDocument();
    // and no invented per-item average alongside it
    expect(screen.queryByText(/per work item/)).toBeNull();
  });
});
