import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../../store";
import type { Analytics as Report } from "../../../types";
import { stubFetch, type Call } from "../../item/testkit";
import { Analytics } from "./Analytics";

const REPORT = {
  totals: {
    work_items: 20, work_items_run: 12, by_status: {}, mrs_merged: 12, wall_ms: 1, human_wait_ms: 14 * 60_000, tokens_in: 1, tokens_out: 1, cost_usd: 38.2, cost_complete: true, rounds: 9, capped_out: 2,
    completed: 12, completed_prev: 9, median_lead_ms: 3600_000, human_wait_pct: 8, fix_cycles: 1.5, fix_cycles_capped: 2, rejected_gates: 1, unplanned_touches_per_item: 0.25, open_mr_to_green_ci_ms: 600_000,
  },
  weekly_merged: [{ week_start: "2026-09-07", n: 3 }],
  by_node: [{ node: "implementation", runs: 18, wall_ms: 1, avg_ms: 1, tokens: 1, cost_usd: 14.2, cost_complete: true, rounds: 1, capped_out: 0 }],
  by_repo: [{ repo: "/code/kraft-plugins", items: 8, mrs: 6, tokens: 1, cost_usd: 20, cost_complete: false, done: 6, cycles: 3 }],
  rejected_gates_by_gate: [],
  stop_reasons: [{ label: "gate wait", n: 5 }],
} as unknown as Report;

function mount(answer: [number, unknown] = [200, REPORT]) {
  const calls = stubFetch({ "GET /analytics": answer });
  render(<MemoryRouter initialEntries={["/analytics"]}><Routes><Route path="/analytics" element={<Analytics />} /></Routes></MemoryRouter>);
  return calls;
}
const tile = (label: string) => screen.getByText(label).closest(".ph-tile") as HTMLElement;
const asked = (calls: Call[]) => calls.filter((c) => c.path === "/analytics");
afterEach(() => vi.unstubAllGlobals());

describe("Analytics (J): each tile reads one field", () => {
  it("shows Spent, Items done, Time at gates and Fix loops from the report", async () => {
    useStore.setState({ workItems: { a: { id: "a", display_status: "running" }, b: { id: "b", display_status: "done" } } } as never);
    mount();
    await screen.findByText("$38.20");
    expect(within(tile("Spent")).getByText("$3.18 per completed item")).toBeInTheDocument();
    expect(within(tile("Items done")).getByText("12")).toBeInTheDocument();
    expect(within(tile("Items done")).getByText("+3 vs previous · 1 running now")).toBeInTheDocument();
    expect(within(tile("Time at gates")).getByText("14m")).toBeInTheDocument();
    expect(within(tile("Time at gates")).getByText("8% of the time waiting on you")).toBeInTheDocument();
    expect(within(tile("Fix loops")).getByText("1.5")).toBeInTheDocument();
    expect(within(tile("Fix loops")).getByText("rounds per fix-loop item · 2 hit their cap")).toBeInTheDocument();
  });

  it("makes its scrolling area reachable from the keyboard (R14b-03)", async () => {
    mount();
    await screen.findByText("$38.20");
    expect(screen.getByRole("region", { name: "Analytics figures" })).toHaveAttribute("tabindex", "0");
  });

  it("does not claim a median, a spend delta or a per-day chart (R66)", async () => {
    mount();
    await screen.findByText("$38.20");
    expect(screen.queryByText(/median wait/i)).toBeNull();
    expect(screen.queryByText(/Spend per day/i)).toBeNull();
    expect(screen.queryByText(/vs last week/i)).toBeNull();
    expect(screen.queryByRole("group", { name: /per day|per week/i })).toBeNull();
  });

  it("marks a partial cost instead of showing it as complete", async () => {
    mount([200, { ...REPORT, totals: { ...REPORT.totals, cost_complete: false } }]);
    expect(await screen.findByText("$38.20+")).toBeInTheDocument();
  });

  it("lists nodes, repos and stop reasons with the desktop's footnotes", async () => {
    mount();
    expect(await screen.findByText("implementation")).toBeInTheDocument();
    expect(screen.getByText("18 runs")).toBeInTheDocument();
    expect(screen.getByText("kraft-plugins")).toBeInTheDocument();
    expect(screen.getByText("$20.00+")).toBeInTheDocument();
    expect(screen.getByText("gate wait")).toBeInTheDocument();
    expect(screen.getByText(/a trailing "\+" marks a sum that is missing a session/)).toBeInTheDocument();
    expect(screen.getByText("0.25 unplanned touches per item · open MR → checks done 10m median")).toBeInTheDocument();
  });

  it("asks for the range the person picks, 7 days first", async () => {
    const calls = mount();
    await screen.findByText("$38.20");
    expect(asked(calls)).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: /7 days/ }));
    const sheet = screen.getByRole("dialog", { name: "Range" });
    expect(within(sheet).getAllByRole("radio").map((r) => r.textContent?.replace("✓", ""))).toEqual(["7 days", "30 days", "90 days"]);
    await userEvent.click(within(sheet).getByRole("radio", { name: "30 days" }));
    await waitFor(() => expect(asked(calls)).toHaveLength(2));
    expect(asked(calls).map((c) => c.query.get("range"))).toEqual(["7d", "30d"]);
    expect(screen.getByRole("button", { name: /30 days/ })).toBeInTheDocument();
  });

  it("says when nothing completed, with no zero-height chart", async () => {
    mount([200, { ...REPORT, totals: { ...REPORT.totals, completed: 0, completed_prev: null, cost_usd: 0 }, by_node: [], by_repo: [], stop_reasons: [] }]);
    expect(await screen.findByText("Nothing has completed in this range yet.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "By node" })).toBeNull();
  });

  it("shows the server's words when the report cannot be read", async () => {
    mount([500, { detail: "analytics offline" }]);
    expect(await screen.findByRole("alert")).toHaveTextContent("analytics offline");
  });
});
