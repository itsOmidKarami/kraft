import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../../api";
import { useStore } from "../../../store";
import type { DisplayStatus, WorkItem } from "../../../types";
import { groupOf, groupsOf } from "../../board/model";
import { resetBoardPrefs } from "../../board/prefs";
import { detail, stubFetch } from "../../item/testkit";
import { Board } from "./Board";
import { useNeedsCount } from "../nav/needsCount";

const item = (id: string, display_status: DisplayStatus, over: Partial<WorkItem> = {}): WorkItem =>
  detail({ id, title: `Item ${id}`, display_status, bead_id: `kraft-${id}`, updated_at: `2026-09-13T0${id.slice(-1)}:00:00Z`, ...over });
const gate = (id: string, over: Partial<WorkItem> = {}) =>
  item(id, "needs_you", { current_node_id: "plan_approval", pending_gate: "plan_approval", stop: { kind: "gate", node: "plan_approval", resume_at: null, reason: null }, ...over });

const put = (...list: WorkItem[]) => {
  useStore.setState({ workItems: Object.fromEntries(list.map((i) => [i.id, i])), connection: "open" } as never);
  vi.spyOn(api, "listWorkItems").mockResolvedValue({ items: list, cursor: 1 });
};
function Where() {
  const l = useLocation();
  return <output aria-label="where">{l.pathname + l.search}{JSON.stringify((l.state as object) ?? null)}</output>;
}
const mount = (path = "/") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/" element={<><Board /><Where /></>} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
const where = () => screen.getByLabelText("where").textContent;

beforeEach(() => {
  resetBoardPrefs();
  vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [{ path: "/r" } as never] });
  vi.spyOn(api, "getTheme").mockResolvedValue({ board: { group_by: "status", show_done: 2, open_in: "peek" } } as never);
  put();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("the phone board (B)", () => {
  it("files items in the desktop's four groups, in its order, and the groups equal groupsOf", async () => {
    const list = [item("r1", "running"), gate("g2"), item("d3", "done"), item("p4", "paused", { current_node_id: null }), item("p5", "paused"), item("f6", "failed")];
    put(...list);
    mount();
    await act(async () => {});
    const heads = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    const want = groupsOf(list, { filter: { q: "", repo: "", chain: "" }, group: "status", sort: "attention", doneCap: 2 }).filter((g) => g.total).map((g) => `${g.label}${g.total}`);
    expect(heads).toEqual(want);
    // Paused mid-chain and failed are under Needs you, a paused item with no node is Not started (R54).
    const needs = screen.getByRole("region", { name: "Needs you" });
    expect(within(needs).getByText("Item p5")).toBeInTheDocument();
    expect(within(needs).getByText("Item f6")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Not started" })).getByText("Item p4")).toBeInTheDocument();
  });

  it("counts the chips from the same rule as the tab badge, and the chip is in the URL", async () => {
    const list = [gate("g1"), item("f2", "failed"), item("r3", "running"), item("d4", "done")];
    put(...list);
    let badge = -1;
    function Probe() { badge = useNeedsCount(); return null; }
    render(<Probe />);
    mount();
    await act(async () => {});
    const needsChip = screen.getByRole("button", { name: /^Needs you/ });
    expect(needsChip).toHaveTextContent(String(badge));
    expect(badge).toBe(list.filter((i) => groupOf(i) === "needs").length);
    await userEvent.click(needsChip);
    expect(where()).toContain("/?f=needs");
    expect(screen.queryByText("Item r3")).toBeNull();
    expect(screen.getByText("Item g1")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^All\d+$/ }));
    expect(where()).not.toContain("f=");
  });

  it("opens an item on a tap of the card", async () => {
    put(item("r1", "running"));
    mount();
    await userEvent.click(await screen.findByText("Item r1"));
    expect(where()).toContain("/work-items/r1");
  });

  it("caps Done at show_done with a Show all row", async () => {
    put(item("d1", "done"), item("d2", "done"), item("d3", "done"));
    mount();
    await screen.findByText("Item d3");
    expect(screen.queryByText("Item d1")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Show all 3" }));
    expect(screen.getByText("Item d1")).toBeInTheDocument();
  });

  it("filters by repo through the sheet and keeps Back on the board", async () => {
    put(item("a1", "running", { repo: "/code/alpha" }), item("b2", "running", { repo: "/code/beta" }));
    mount();
    await screen.findByText("Item a1");
    await userEvent.click(screen.getByRole("button", { name: /All repos/ }));
    const sheet = screen.getByRole("dialog", { name: "Repo" });
    expect(within(sheet).getByRole("radio", { name: /All repos/, checked: true })).toBeInTheDocument();
    await userEvent.click(within(sheet).getByRole("radio", { name: /beta/ }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(where()).toContain("repo=%2Fcode%2Fbeta");
    expect(screen.queryByText("Item a1")).toBeNull();
    expect(screen.getByText("Item b2")).toBeInTheDocument();
  });

  it("says why it is empty: no items, a filter with no match, no repo connected", async () => {
    mount();
    expect(await screen.findByText("Nothing here. Tap + to file one.")).toBeInTheDocument();
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [] });
    mount();
    // `kraft repo connect` connects one; `kraft admin init` never does, and
    // More › Repos on a phone cannot connect a path either.
    expect(await screen.findByText(/No repository is connected/)).toHaveTextContent(
      "No repository is connected. Run kraft repo connect in a repo on the machine, or connect one from a computer.",
    );
    expect(screen.queryByRole("link", { name: "More › Repos" })).toBeNull();
  });

  it("says offline beside the title when the list read fails, and keeps the rows", async () => {
    put(item("r1", "running"));
    vi.spyOn(useStore.getState(), "bootstrap").mockRejectedValue(new Error("down"));
    mount();
    expect(await screen.findByText("offline")).toBeInTheDocument();
    expect(screen.getByText("Item r1")).toBeInTheDocument();
  });
});

describe("a card's inline actions (B.4)", () => {
  it("gate stop: Approve calls act.approve once, Reject… opens the reject composer", async () => {
    put(gate("g1"));
    const calls = stubFetch();
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toEqual([{ method: "POST", path: "/work-items/g1/gates/plan_approval/approve", body: {} }]));
    await userEvent.click(screen.getByRole("button", { name: "Reject…" }));
    expect(where()).toContain("/work-items/g1?compose=reject");
  });

  it("a refusal that needs the document opens the gate review; another refusal stays on the card", async () => {
    put(gate("g1"), gate("g2"));
    stubFetch({
      "POST /work-items/g1/gates/plan_approval/approve": [409, { detail: "plan_approval: approving a chain revision needs the digest of the one you reviewed" }],
      "POST /work-items/g2/gates/plan_approval/approve": [409, { detail: "gate 'plan_approval' is not pending" }],
    });
    mount();
    await screen.findByText("Item g1");
    const approve = (id: string) => within(document.querySelector(`[data-row="${id}"]`) as HTMLElement).getByRole("button", { name: "Approve" });
    await userEvent.click(approve("g2"));
    expect(await screen.findByRole("alert")).toHaveTextContent("is not pending");
    expect(where()).toBe("/null");
    await userEvent.click(approve("g1"));
    await waitFor(() => expect(where()).toContain("/work-items/g1/review?gate=plan_approval"));
  });

  it("cap, budget, question, failed and paused each get their one button", async () => {
    put(
      item("c1", "needs_you", { stop: { kind: "cap", node: "verification", resume_at: null, reason: null } }),
      item("b2", "needs_you", { stop: { kind: "budget", node: "verification", resume_at: null, reason: null } }),
      item("q3", "needs_you", { stop: { kind: "question", node: "verification", resume_at: null, reason: "needs_context: allow it?" } }),
      item("f4", "failed", { stop: { kind: "failed", node: "verification", resume_at: null, reason: null } }),
      item("p5", "paused"),
      item("r6", "running"),
    );
    const calls = stubFetch();
    mount();
    await screen.findByText("Item c1");
    const card = (id: string) => within(document.querySelector(`[data-row="${id}"]`) as HTMLElement);
    expect(card("c1").getByRole("button", { name: "Open" })).toBeInTheDocument();
    // A list row cannot tell the item's own cap from a daily or token one, which the server will not raise: the item screen can.
    expect(card("b2").getByRole("button", { name: "Open" })).toBeInTheDocument();
    expect(card("b2").queryByRole("button", { name: /Raise/ })).toBeNull();
    expect(card("q3").getByRole("button", { name: "Answer…" })).toBeInTheDocument();
    expect(card("f4").getByRole("button", { name: "Retry…" })).toBeInTheDocument();
    // A running card has its tap target and nothing inline.
    expect(card("r6").getAllByRole("button")).toHaveLength(1);

    await userEvent.click(card("b2").getByRole("button", { name: "Open" }));
    expect(where()).toContain("/work-items/b2");
    expect(calls).toEqual([]);
  });

  it("paused mid-chain: Resume calls act.resume", async () => {
    put(item("p5", "paused"));
    const calls = stubFetch();
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Resume" }));
    await waitFor(() => expect(calls.at(-1)).toEqual({ method: "POST", path: "/work-items/p5/resume", body: { steer: null } }));
  });

  it("disables the buttons while offline", async () => {
    put(gate("g1"));
    stubFetch();
    vi.spyOn(useStore.getState(), "bootstrap").mockRejectedValue(new Error("down"));
    mount();
    expect(await screen.findByRole("button", { name: "Approve" })).toBeDisabled();
  });
});
