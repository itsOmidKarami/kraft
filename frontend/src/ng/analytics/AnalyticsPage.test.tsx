import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { Analytics } from "../../types";
import { HeaderTailHost } from "../shell/HeaderActions";
import { AnalyticsPage } from "./AnalyticsPage";

const REPORT: Analytics = {
  totals: { work_items: 42, work_items_run: 39, by_status: {}, mrs_merged: 27, wall_ms: 3e8, human_wait_ms: 8e7, tokens_in: 3.9e7, tokens_out: 4.2e6, cost_usd: 212.4, cost_complete: true, rounds: 140, capped_out: 6, completed: 30, completed_prev: 22, median_lead_ms: 5.6e6, human_wait_pct: 25, fix_cycles: 1.5, fix_cycles_capped: 6, rejected_gates: 9, unplanned_touches_per_item: 1.25, open_mr_to_green_ci_ms: 1.2e6 },
  weekly_merged: [],
  by_node: [{ node: "implementation", runs: 30, wall_ms: 4e7, avg_ms: 1.2e6, tokens: 4e6, cost_usd: 21.5, cost_complete: true, rounds: 12, capped_out: 0 }],
  by_repo: [{ repo: "/Users/dev/code/kraft", items: 14, mrs: 9, tokens: 1.2e7, cost_usd: 70.2, cost_complete: true, done: 9, cycles: 30 }],
  rejected_gates_by_gate: [],
  stop_reasons: [{ label: "agent needs context", n: 2 }],
};

const show = async (r: Analytics) => {
  vi.spyOn(api, "getAnalytics").mockResolvedValue(r);
  render(<AnalyticsPage />);
  await screen.findByRole("heading", { name: "Overview" });
};

afterEach(() => vi.restoreAllMocks());

const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "analytics.css"), "utf-8");

// What the shipped views/Analytics.tsx shows that this page must too (GAP §2 #39).
const SECTIONS = ["Overview", "Throughput by week", "By node", "By repo", "Why items stopped for a person"];
const FOOTNOTES = [
  /Minutes are the median per completed item/,
  /a trailing "\+" marks a sum that is missing a session/,
  /bars count merges, not completions/,
  /unplanned touches per item · open MR → green CI/,
  /current week is partial/,
];
const TILES = ["Completed", "Lead time", "Cost"];

describe("ng AnalyticsPage", () => {
  it("has every section, tile and footnote the shipped page has", async () => {
    await show(REPORT);
    for (const name of SECTIONS) expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    for (const label of TILES) expect(screen.getByText(label, { selector: ".an-kpi-label" })).toBeInTheDocument();
    for (const note of FOOTNOTES) expect(screen.getByText(note)).toBeVisible();
    expect(screen.getByText("+8 vs previous · $7.08 each")).toBeInTheDocument();
    expect(screen.getByText("median create → merge · 25% waiting on you")).toBeInTheDocument();
    expect(screen.getByText("1.5 fix cycles per verify · 6 capped · 9 rejected gates")).toBeInTheDocument();
  });

  it("says its scope once, in the header row, with no heading of its own (AN-1)", async () => {
    vi.spyOn(api, "getAnalytics").mockResolvedValue(REPORT);
    const tail = document.createElement("div");
    document.body.append(tail);
    const { container } = render(<HeaderTailHost.Provider value={tail}><AnalyticsPage /></HeaderTailHost.Provider>);
    await screen.findByRole("heading", { name: "Overview" });
    expect(tail).toHaveTextContent("· Last 8 weeks · completed work items");
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(container).not.toHaveTextContent("Last 8 weeks");
    tail.remove();
  });

  it("puts the chart and each table in a bordered panel under a heading (AN-4)", async () => {
    await show(REPORT);
    for (const name of ["Throughput by week", "By node", "By repo", "Why items stopped for a person"]) {
      const section = screen.getByRole("heading", { name }).closest("section")!;
      expect(section.querySelector(":scope > .an-panel")).not.toBeNull();
    }
    expect(css).toMatch(/\.an-page\s*\{[^}]*max-width: 680px/);
    expect(css).toMatch(/\.an-panel\s*\{[^}]*border: 1px solid[^}]*border-radius: 12px/);
    expect(css).toMatch(/\.an-section h2\s*\{[^}]*font-size: 16px/);
  });

  it("asks for the last eight weeks, filtered by repo and chain only", async () => {
    await show(REPORT);
    expect(api.getAnalytics).toHaveBeenCalledWith({ range: "8w", repo: undefined, template: undefined });
  });

  it("gives the throughput chart a table and keyboard-reachable bars", async () => {
    await show(REPORT);
    const bars = within(screen.getByRole("group", { name: "Merged items per week" })).getAllByLabelText(/^Week of /);
    expect(bars).toHaveLength(8);
    bars.forEach((b) => expect(b).toHaveAttribute("tabindex", "0"));
    expect(screen.getByRole("table", { name: "Merged items per week" })).toBeInTheDocument();
  });

  it("says what is missing instead of drawing empty charts", async () => {
    await show({ ...REPORT, totals: { ...REPORT.totals, mrs_merged: 0, completed: 0, completed_prev: null }, by_node: [], by_repo: [], stop_reasons: [] });
    expect(screen.getByText("Nothing merged in this range.")).toBeInTheDocument();
    expect(screen.getAllByText("Nothing here yet.")).toHaveLength(2);
    expect(screen.getByText("Nothing stopped for a person in this range.")).toBeInTheDocument();
    expect(screen.getByText("no previous period")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Merged items per week" })).toBeNull();
  });

  it("names a long repo and node once, whole in its title", async () => {
    const long = "very-long-repository-name-".repeat(6);
    await show({ ...REPORT, by_repo: [{ ...REPORT.by_repo[0], repo: `/Users/dev/${long}` }], stop_reasons: [{ label: long, n: 1 }] });
    expect(screen.getByTitle(`/Users/dev/${long}`)).toHaveAttribute("data-allow-ellipsis");
    expect(screen.getByTitle(long)).toHaveAttribute("data-allow-ellipsis");
  });

  it("shows the error and no report when the request fails", async () => {
    vi.spyOn(api, "getAnalytics").mockRejectedValue(new Error("boom"));
    render(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("boom"));
    expect(screen.queryByRole("heading", { name: "Overview" })).toBeNull();
  });
});

describe("analytics sources", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  it("write no colour literal in the page", () => {
    expect(readFileSync(join(here, "AnalyticsPage.tsx"), "utf-8")).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(|oklch\(/i);
  });
});
