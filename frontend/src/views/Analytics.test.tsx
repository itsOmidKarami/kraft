import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { elapsed, usd } from "../format";
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
    cost_usd: 18.0,
    cost_complete: true,
    rounds: 5,
    capped_out: 2,
    completed: 6,
    completed_prev: 4,
    median_lead_ms: 2 * 3_600_000 + 41 * 60_000,
    human_wait_pct: 38,
    fix_cycles: 1.6,
    fix_cycles_capped: 9,
    rejected_gates: 7,
    unplanned_touches_per_item: 0.4,
    open_mr_to_green_ci_ms: 45 * 60_000,
  },
  weekly_merged: [{ week_start: "2026-08-31", n: 4 }],
  by_node: [
    {
      node: "implementation",
      runs: 9,
      wall_ms: 900_000,
      avg_ms: 100_000,
      tokens: 1_000_000,
      cost_usd: 15,
      cost_complete: true,
      rounds: 5,
      capped_out: 2,
    },
    {
      node: "verify",
      runs: 12,
      wall_ms: 120_000,
      avg_ms: 10_000,
      tokens: 400_000,
      cost_usd: 3,
      cost_complete: true,
      rounds: 5,
      capped_out: 0,
    },
  ],
  by_repo: [
    {
      repo: "/repo-a",
      items: 9,
      mrs: 4,
      tokens: 1_400_000,
      cost_usd: 18,
      cost_complete: true,
      done: 6,
      cycles: 1.2,
    },
  ],
  rejected_gates_by_gate: [
    { gate: "plan_approval", n: 5 },
    { gate: "human_review_approval", n: 2 },
  ],
  stop_reasons: [
    { label: "gate · plan_approval", n: 41 },
    { label: "capped out · verify_fix_loop", n: 9 },
    { label: "agent question · needs_context", n: 4 },
    { label: "budget", n: 2 },
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
    <MemoryRouter>
      <AnalyticsView />
    </MemoryRouter>,
  );

describe("AnalyticsView", () => {
  it("asks for the last 8 weeks and the current filters", async () => {
    renderView();
    await screen.findByText("Analytics");
    expect(api.getAnalytics).toHaveBeenCalledWith({
      range: "8w",
      repo: undefined,
      template: undefined,
    });
    expect(screen.getByText("last 8 weeks")).toBeInTheDocument();
    expect(screen.queryByText(/last 7 days/i)).toBeNull();
  });

  it("shows exactly three headline tiles, the removed ones folded into their sub-lines (W11 · E)", async () => {
    const { container } = renderView();
    await screen.findAllByText("6");
    expect(container.querySelectorAll(".kpi")).toHaveLength(3);
    const text = (sel: string) => [...container.querySelectorAll(sel)].map((n) => n.textContent);
    expect(text(".kpi-label")).toEqual(["Completed", "Lead time", "Cost"]);
    expect(text(".kpi-value")).toEqual(["6", elapsed(report.totals.median_lead_ms), usd(18, true)]);
    expect(text(".kpi-sub")).toEqual([
      `+2 vs previous · ${usd(3)} each`,
      "median create → merge · 38% waiting on you",
      "1.6 fix cycles per verify · 9 capped · 7 rejected gates",
    ]);
  });

  it("puts unplanned touches and MR → green CI in the stop-reasons footer", async () => {
    const { container } = renderView();
    await screen.findByText("Why items stopped for a person");
    expect(container.querySelector(".stop-reasons .table-foot")).toHaveTextContent(
      `unplanned touches 0.40 per item · open MR → green CI ${elapsed(report.totals.open_mr_to_green_ci_ms)} median`,
    );
  });

  it("W8.4: the chart is empty exactly when the range completed nothing, like the tiles", async () => {
    vi.spyOn(api, "getAnalytics").mockResolvedValue({
      ...report,
      totals: { ...report.totals, completed: 0, completed_prev: 0 },
    });
    const { container } = renderView();
    expect(await screen.findByText("nothing completed in this range")).toBeInTheDocument();
    expect(container.querySelectorAll(".bar-col")).toHaveLength(0);
    expect(container.querySelector(".kpi-value")).toHaveTextContent("0");
    expect(container.querySelector(".kpi-sub")).toHaveTextContent(/^\+0 vs previous$/);
  });

  it("sizes the by-node and by-repo name columns to their names, the share bar gives (W12.4)", () => {
    const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "analytics.css"), "utf-8");
    expect(css).toMatch(/\.table-block\.by-node \{[^}]*grid-template-columns: minmax\(14ch, max-content\) minmax\(40px, 1fr\) 40px 60px 60px 44px;/);
    expect(css).toMatch(/\.table-block\.by-repo \{[^}]*grid-template-columns: minmax\(14ch, max-content\) minmax\(44px, 1fr\) 44px 60px 50px;/);
    expect(css).toMatch(/\.node-row, \.repo-row \{ grid-template-columns: subgrid; \}/);
  });

  it("draws eight week columns", async () => {
    const { container } = renderView();
    await screen.findAllByText("6");
    expect(container.querySelectorAll(".bar-col")).toHaveLength(8);
  });

  it("refetches when repo/template selects change", async () => {
    renderView();
    await screen.findAllByText("6");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /repo/i }), "/repo-b");
    expect(api.getAnalytics).toHaveBeenLastCalledWith({
      range: "8w",
      repo: "/repo-b",
      template: undefined,
    });
  });

  it("renders the by-node MIN/%% columns and by-repo DONE/CYCLES columns", async () => {
    const { container } = renderView();
    await screen.findByText("implementation");
    const nodeRow = container.querySelector('.node-row[data-node="implementation"]')!;
    expect(nodeRow.querySelector('[data-label="min"]')).not.toBeNull();
    expect(nodeRow.querySelector('[data-label="%"]')).not.toBeNull();
    const repoRow = container.querySelector('.repo-row[data-repo="/repo-a"]')!;
    expect(repoRow.querySelector('[data-label="done"]')).not.toBeNull();
    expect(repoRow.querySelector('[data-label="cycles"]')).not.toBeNull();
  });

  it("orders stop reasons by count and labels each bar", async () => {
    const { container } = renderView();
    await screen.findByText("Why items stopped for a person");
    const rows = [...container.querySelectorAll(".stop-row")];
    expect(rows.map((r) => r.getAttribute("data-label"))).toEqual(
      report.stop_reasons.map((s) => s.label),
    );
    expect(rows.map((r) => r.querySelector(".stop-n")!.textContent)).toEqual(
      report.stop_reasons.map((s) => String(s.n)),
    );
  });

  it("places stop-reasons in the right column, under By repo, not full-width at the bottom", async () => {
    const { container } = renderView();
    await screen.findByText("Why items stopped for a person");
    const col = container.querySelector(".table-col")!;
    expect(col.querySelector(".by-repo")).not.toBeNull();
    expect(col.querySelector(".stop-reasons")).not.toBeNull();
  });

  it("puts the repo/template filters in the page head, not a separate bar", async () => {
    const { container } = renderView();
    await screen.findByText("Analytics");
    expect(container.querySelector(".analytics-head .analytics-filters")).not.toBeNull();
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
  });

  it("an empty by-node or by-repo table is one line, not a header row over nothing (W4.7)", async () => {
    vi.spyOn(api, "getAnalytics").mockResolvedValue({ ...report, by_node: [], by_repo: [] });
    const { container } = renderView();
    expect(await screen.findAllByText("nothing here yet")).toHaveLength(2);
    expect(container.querySelector(".node-head")).toBeNull();
    expect(container.querySelector(".repo-head")).toBeNull();
  });
});
