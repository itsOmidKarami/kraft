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
  rejected_gates_by_gate: [{ gate: "plan_approval", n: 1 }],
  gate_waits: [{ gate: "plan_approval", n: 4, median_ms: 1800_000, total_ms: 9e6 }],
  stop_reasons: [{ label: "gate wait", n: 5 }, { label: "gate · spec_approval", n: 3 }, { label: "budget", n: 2 }],
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
  it("shows Completed, Lead time, Cost and Repairs per item from the report, one short line each", async () => {
    mount();
    await screen.findByText("$38.20");
    expect(within(tile("Completed")).getByText("12")).toBeInTheDocument();
    expect(within(tile("Completed")).getByText("+3 vs previous")).toBeInTheDocument();
    expect(within(tile("Lead time")).getByText("1h")).toBeInTheDocument();
    expect(within(tile("Lead time")).getByText("median")).toBeInTheDocument();
    expect(within(tile("Waiting on you")).getByText("8%")).toBeInTheDocument();
    expect(within(tile("Waiting on you")).getByText("of lead time")).toBeInTheDocument();
    expect(within(tile("Cost")).getByText("$3.18 per completed item")).toBeInTheDocument();
    expect(within(tile("Repairs per item")).getByText("1.5")).toBeInTheDocument();
    expect(within(tile("Repairs per item")).getByText("2 hit their cap")).toBeInTheDocument();
  });

  it("links a repo row to the board for that repo and the gate and stop rows to the board", async () => {
    mount();
    await screen.findByText("$38.20");
    expect(screen.getByRole("link", { name: /kraft-plugins/ })).toHaveAttribute("href", "/?repo=%2Fcode%2Fkraft-plugins");
    expect(screen.getByRole("link", { name: /approve plan/ })).toHaveAttribute("href", "/?gate=plan_approval");
    // The server's bucket is "gate · spec_approval"; the page names the gate as the board does, and still filters by its id.
    expect(screen.getByRole("link", { name: /gate · approve spec/ })).toHaveAttribute("href", "/?gate=spec_approval");
    expect(screen.getByRole("link", { name: /budget/ })).toHaveAttribute("href", "/?stop=budget");
    expect(screen.getByRole("link", { name: /gate wait/ })).toHaveAttribute("href", "/");
  });

  it("reads a lead time and a checks span nothing measured as a dash, not 0s", async () => {
    mount([200, { ...REPORT, totals: { ...REPORT.totals, completed: 0, median_lead_ms: 0, open_mr_to_green_ci_ms: 0 }, by_node: [{ ...REPORT.by_node[0] }] }]);
    await screen.findByText("$38.20");
    expect(within(tile("Lead time")).getByText("—")).toBeInTheDocument();
    expect(screen.getByText(/open MR → checks done — median/)).toBeInTheDocument();
  });

  it("makes its scrolling area reachable from the keyboard (R14b-03)", async () => {
    mount();
    await screen.findByText("$38.20");
    expect(screen.getByRole("region", { name: "Analytics figures" })).toHaveAttribute("tabindex", "0");
  });

  it("does not claim a spend delta or a per-day chart (R66)", async () => {
    mount();
    await screen.findByText("$38.20");
    expect(screen.queryByText(/Spend per day/i)).toBeNull();
    expect(screen.queryByText(/vs last week/i)).toBeNull();
    expect(screen.queryByRole("group", { name: /per day|per week/i })).toBeNull();
  });

  it("marks a partial cost instead of showing it as complete", async () => {
    mount([200, { ...REPORT, totals: { ...REPORT.totals, cost_complete: false } }]);
    expect(await screen.findByText("$38.20+")).toBeInTheDocument();
  });

  it("lists gates, nodes, repos and stop reasons, and keeps the footnotes behind one disclosure", async () => {
    mount();
    expect(await screen.findByText("implementation")).toBeInTheDocument();
    expect(screen.getByText("approve plan")).toBeInTheDocument();
    expect(screen.getByText("4 waits · median 30m · 1 rejected")).toBeInTheDocument();
    expect(screen.getByText("2h 30m")).toBeInTheDocument();
    expect(screen.getByText("18 runs")).toBeInTheDocument();
    expect(screen.getByText("kraft-plugins")).toBeInTheDocument();
    expect(screen.getByText("$20.00+")).toBeInTheDocument();
    expect(screen.getByText("8 items · 6 done · avg repairs 3.0")).toBeInTheDocument();
    expect(screen.getByText("gate wait")).toBeInTheDocument();
    expect(screen.getByText("Items created in the last 8 weeks")).toBeInTheDocument();
    expect(screen.getByText("0.25 unplanned touches per item · open MR → checks done 10m median")).toBeInTheDocument();
    const how = /Tokens are as recorded, never estimated\. Dollars are what agents reported plus Kraft's estimate from its price table where they reported none, and a session it cannot price puts a "\+" on the sum/;
    expect(screen.getByText(how)).not.toBeVisible();
    await userEvent.click(screen.getByText("How this is counted"));
    expect(screen.getByText(how)).toBeVisible();
  });

  it("asks for the range the person picks, 8 weeks first, as the desktop does", async () => {
    const calls = mount();
    await screen.findByText("$38.20");
    expect(asked(calls)).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: /8 weeks/ }));
    const sheet = screen.getByRole("dialog", { name: "Range" });
    expect(within(sheet).getAllByRole("radio").map((r) => r.textContent?.replace("✓", ""))).toEqual(["7 days", "30 days", "8 weeks", "90 days"]);
    await userEvent.click(within(sheet).getByRole("radio", { name: "30 days" }));
    await waitFor(() => expect(asked(calls)).toHaveLength(2));
    expect(asked(calls).map((c) => c.query.get("range"))).toEqual(["8w", "30d"]);
    expect(screen.getByRole("button", { name: /30 days/ })).toBeInTheDocument();
    expect(await screen.findByText("Items created in the last 30 days")).toBeInTheDocument();
  });

  it("narrows by the repo and chain the person picks, as the desktop does", async () => {
    useStore.setState({ workItems: { a: { id: "a", repo: "/code/kraft-plugins", chain_template: "quick-task" }, b: { id: "b", repo: "/code/kraft", chain_template: null } } } as never);
    const calls = mount();
    await screen.findByText("$38.20");
    await userEvent.click(screen.getByRole("button", { name: /All repos/ }));
    await userEvent.click(within(screen.getByRole("dialog", { name: "Repo" })).getByRole("radio", { name: /kraft-plugins/ }));
    await waitFor(() => expect(asked(calls)).toHaveLength(2));
    await userEvent.click(screen.getByRole("button", { name: /All chains/ }));
    await userEvent.click(within(screen.getByRole("dialog", { name: "Chain" })).getByRole("radio", { name: /quick-task/ }));
    await waitFor(() => expect(asked(calls)).toHaveLength(3));
    expect(asked(calls).map((c) => [c.query.get("repo"), c.query.get("template")])).toEqual([[null, null], ["/code/kraft-plugins", null], ["/code/kraft-plugins", "quick-task"]]);
    expect(await screen.findByRole("link", { name: /approve plan/ })).toHaveAttribute("href", "/?repo=%2Fcode%2Fkraft-plugins&chain=quick-task&gate=plan_approval");
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
