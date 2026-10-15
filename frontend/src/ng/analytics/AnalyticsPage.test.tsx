import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { Analytics } from "../../types";
import { HeaderActionsHost, HeaderTailHost } from "../shell/HeaderActions";
import { AnalyticsPage } from "./AnalyticsPage";

const REPORT: Analytics = {
  totals: { work_items: 42, work_items_run: 39, by_status: {}, mrs_merged: 27, wall_ms: 3e8, human_wait_ms: 8e7, tokens_in: 3.9e7, tokens_out: 4.2e6, cost_usd: 212.4, cost_complete: true, rounds: 140, capped_out: 6, completed: 30, completed_prev: 22, median_lead_ms: 5.6e6, human_wait_pct: 25, fix_cycles: 1.5, fix_cycles_capped: 6, rejected_gates: 9, unplanned_touches_per_item: 1.25, open_mr_to_green_ci_ms: 1.2e6 },
  weekly_merged: [],
  by_node: [{ node: "implementation", runs: 30, wall_ms: 4e7, avg_ms: 1.2e6, tokens: 4e6, cost_usd: 21.5, cost_complete: true, rounds: 12, capped_out: 0 }],
  by_repo: [{ repo: "/Users/dev/code/kraft", items: 14, mrs: 9, tokens: 1.2e7, cost_usd: 70.2, cost_complete: true, done: 9, cycles: 2 }],
  rejected_gates_by_gate: [{ gate: "spec_approval", n: 2 }],
  gate_waits: [{ gate: "spec_approval", n: 3, median_ms: 3.6e6, total_ms: 1.2e7 }],
  stop_reasons: [{ label: "agent needs context", n: 2 }],
};

const show = async (r: Analytics) => {
  vi.spyOn(api, "getAnalytics").mockResolvedValue(r);
  render(<AnalyticsPage />);
  await screen.findByRole("heading", { name: "Overview" });
};

afterEach(() => vi.restoreAllMocks());

const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "analytics.css"), "utf-8");

const SECTIONS = ["Overview", "Throughput by week", "Waiting at each gate", "By node", "By repo", "Why items stopped for a person"];
const FOOTNOTES = [
  /Minutes are the mean wall time of one worker session, over every item in range/,
  /Tokens are as recorded, never estimated\. Dollars are what agents reported plus Kraft's estimate from its price table where they reported none, and a session it cannot price puts a "\+" on the sum/,
  /Bars count merges, not completions/,
  /A wait at a gate runs from the gate being requested until it is approved or rejected/,
];
const TILES = ["Completed", "Lead time", "Cost", "Repairs per item"];

describe("ng AnalyticsPage", () => {
  it("has every section and tile, one short line under each tile, and the footnotes in one disclosure", async () => {
    await show(REPORT);
    for (const name of SECTIONS) expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    for (const label of TILES) expect(screen.getByText(label, { selector: ".an-kpi-label" })).toBeInTheDocument();
    expect(screen.getByText("+8 vs previous")).toBeInTheDocument();
    expect(screen.getByText("median · 25% waiting on you")).toBeInTheDocument();
    expect(screen.getByText("$7.08 per completed item")).toBeInTheDocument();
    expect(screen.getByText("6 hit their cap")).toBeInTheDocument();
    expect(screen.getByText(/1\.25 unplanned touches per item · open MR → checks done/)).toBeVisible();
    for (const note of FOOTNOTES) expect(screen.getByText(note)).not.toBeVisible();
    await userEvent.click(screen.getByText("How this is counted"));
    for (const note of FOOTNOTES) expect(screen.getByText(note)).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "avg repairs" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "2.0" })).toBeInTheDocument();
  });

  it("names the gate items waited at, with its median, total and rejections", async () => {
    await show(REPORT);
    const row = screen.getByText("spec_approval").closest("tr")!;
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual(["spec_approval", "", "3", "1h", "3h 20m", "2"]);
  });

  it("says its scope once, in the header row, with no visible heading of its own (AN-1)", async () => {
    vi.spyOn(api, "getAnalytics").mockResolvedValue(REPORT);
    const tail = document.createElement("div");
    document.body.append(tail);
    const { container } = render(<HeaderTailHost.Provider value={tail}><AnalyticsPage /></HeaderTailHost.Provider>);
    await screen.findByRole("heading", { name: "Overview" });
    expect(tail).toHaveTextContent("· Items created in the last 8 weeks");
    // One heading for a screen reader (R14b-03), which sighted readers do not see twice: the crumb says it.
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Analytics");
    expect(screen.getByRole("heading", { level: 1 })).toHaveClass("an-visually-hidden");
    expect(container).not.toHaveTextContent("8 weeks");
    tail.remove();
  });

  it("puts the chart and each table in a bordered panel under a heading, across the page's width (AN-4)", async () => {
    await show(REPORT);
    for (const name of ["Throughput by week", "Waiting at each gate", "By node", "By repo", "Why items stopped for a person"]) {
      const section = screen.getByRole("heading", { name }).closest("section")!;
      expect(section.querySelector(":scope > .an-panel")).not.toBeNull();
    }
    expect(css).toMatch(/\.an-page\s*\{[^}]*max-width: 1180px/);
    expect(css).toMatch(/\.an-panel\s*\{[^}]*border: 1px solid[^}]*border-radius: 12px/);
    expect(css).toMatch(/\.an-section h2\s*\{[^}]*font-size: 16px/);
  });

  it("asks for the last eight weeks first, and for the range the person picks", async () => {
    vi.spyOn(api, "getAnalytics").mockResolvedValue(REPORT);
    const actions = document.createElement("div");
    document.body.append(actions);
    const tail = document.createElement("div");
    document.body.append(tail);
    render(<HeaderActionsHost.Provider value={actions}><HeaderTailHost.Provider value={tail}><AnalyticsPage /></HeaderTailHost.Provider></HeaderActionsHost.Provider>);
    await screen.findByRole("heading", { name: "Overview" });
    expect(api.getAnalytics).toHaveBeenLastCalledWith({ range: "8w", repo: undefined, template: undefined });
    await userEvent.click(within(actions).getByRole("button", { name: /Range/ }));
    expect(screen.getAllByRole("menuitemradio").map((r) => r.textContent?.replace("✓", ""))).toEqual(["7 days", "30 days", "8 weeks", "90 days"]);
    await userEvent.click(screen.getByRole("menuitemradio", { name: "7 days" }));
    await waitFor(() => expect(api.getAnalytics).toHaveBeenLastCalledWith({ range: "7d", repo: undefined, template: undefined }));
    expect(tail).toHaveTextContent("· Items created in the last 7 days");
    actions.remove();
    tail.remove();
  });

  it("gives the throughput chart a table and keyboard-reachable bars", async () => {
    await show(REPORT);
    const bars = within(screen.getByRole("group", { name: "Merged items per week" })).getAllByLabelText(/^Week of /);
    expect(bars).toHaveLength(8);
    bars.forEach((b) => expect(b).toHaveAttribute("tabindex", "0"));
    expect(screen.getByRole("table", { name: "Merged items per week" })).toBeInTheDocument();
  });

  it.each([
    { id: "under a minute", node: { runs: 4, avg_ms: 20_000 }, cell: "<1 min" },
    { id: "a true zero", node: { runs: 4, avg_ms: 0 }, cell: "0" },
    { id: "no runs", node: { runs: 0, avg_ms: 0, cost_usd: 0, cost_complete: false }, cell: "—" },
    { id: "whole minutes", node: { runs: 4, avg_ms: 1.2e6 }, cell: "20" },
  ])("reads a node's mean as $cell for $id, and draws no cost bar where there is no data", async ({ node, cell }) => {
    await show({ ...REPORT, by_node: [{ ...REPORT.by_node[0], ...node }] });
    const row = document.querySelector<HTMLElement>("tr[data-node=implementation]")!;
    expect(row.querySelectorAll("td")[2].textContent).toBe(cell);
    expect(!!row.querySelector(".an-share")).toBe(cell !== "—");
  });

  it("reads a span nothing measured as a dash, not 0s", async () => {
    await show({ ...REPORT, totals: { ...REPORT.totals, completed: 0, median_lead_ms: 0, open_mr_to_green_ci_ms: 0 } });
    expect(screen.getByText("Lead time").parentElement).toHaveTextContent("—");
    expect(screen.getByText(/open MR → checks done — median/)).toBeInTheDocument();
  });

  it("collapses an empty section to one line under its heading, in no panel", async () => {
    await show({ ...REPORT, totals: { ...REPORT.totals, mrs_merged: 0, completed: 0, completed_prev: null }, by_node: [], by_repo: [], gate_waits: [], stop_reasons: [] });
    expect(screen.getByText("Nothing merged in this range.")).toBeInTheDocument();
    expect(screen.getByText("No gate waits in this range.")).toBeInTheDocument();
    expect(screen.getAllByText("Nothing here yet.")).toHaveLength(2);
    expect(screen.getByText("Nothing stopped for a person in this range.")).toBeInTheDocument();
    expect(screen.getByText("no previous period")).toBeInTheDocument();
    expect(screen.getByText("nothing completed yet")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Merged items per week" })).toBeNull();
    expect(document.querySelector(".an-panel")).toBeNull();
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
