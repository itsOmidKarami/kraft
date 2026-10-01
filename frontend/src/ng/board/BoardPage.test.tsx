import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import type { DisplayStatus, WorkItem } from "../../types";
import { detail, stubFetch } from "../item/testkit";
import { Shell } from "../shell/Shell";
import { BoardPage } from "./BoardPage";
import { useBulk } from "./bulk";
import { resetBoardPrefs } from "./prefs";

const item = (id: string, display_status: DisplayStatus, over: Partial<WorkItem> = {}): WorkItem =>
  detail({ id, title: `Item ${id}`, display_status, bead_id: `kraft-${id}`, updated_at: `2026-09-13T0${id.slice(-1)}:00:00Z`, ...over });
/** The store's items, and what the board's own read on mount answers. */
const put = (...list: WorkItem[]) => {
  useStore.setState({ workItems: Object.fromEntries(list.map((i) => [i.id, i])), connection: "open" } as never);
  vi.spyOn(api, "listWorkItems").mockResolvedValue({ items: list, cursor: 1 });
};

const Where = () => {
  const l = useLocation();
  return <span data-testid="where">{l.pathname + l.search}</span>;
};
/** The board inside the Shell, so its header tail and actions have somewhere to go. */
const board = (path = "/") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/" element={<><BoardPage /><Where /></>} />
          <Route path="*" element={<Where />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
const where = () => screen.getByTestId("where").textContent;
const theme = (board: Partial<{ group_by: string; show_done: number; open_in: string }> = {}) =>
  vi.spyOn(api, "getTheme").mockResolvedValue({ board: { group_by: "status", show_done: 2, open_in: "peek", ...board } } as never);

beforeEach(() => {
  resetBoardPrefs();
  vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [{ path: "/r" } as never] });
  vi.spyOn(api, "getPolicy").mockResolvedValue({ archive: { after_days: 30 } } as never);
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok" } as never);
  theme();
  put();
  useBulk.setState({ last: null });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("BoardPage", () => {
  it("shows first-run only when no repo is connected", async () => {
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [] });
    board();
    expect(await screen.findByRole("heading", { name: "Nothing on the board yet" })).toBeInTheDocument();
  });

  it("keeps the board while the repo list is unknown or non-empty", async () => {
    vi.spyOn(api, "getRepos").mockRejectedValue(new Error("down"));
    board();
    await act(async () => {});
    expect(screen.getByRole("region", { name: "Needs you" })).toBeInTheDocument();
  });

  it("files the store's items in the four groups and caps Done at show_done, with show all", async () => {
    put(item("n1", "needs_you"), item("r1", "running"), item("p1", "paused", { current_node_id: null }), ...["d1", "d2", "d3"].map((d) => item(d, "done")));
    board();
    await act(async () => {});
    const done = screen.getByRole("region", { name: "Done" });
    expect(within(screen.getByRole("region", { name: "Needs you" })).getByText("Item n1")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Not started" })).getByText("Item p1")).toBeInTheDocument();
    expect(within(done).queryByText("Item d1")).toBeNull();
    await userEvent.click(within(done).getByRole("button", { name: "show all 3" }));
    expect(within(done).getByText("Item d1")).toBeInTheDocument();
  });

  it("counts NEED YOU in the header and links Done's auto-archive line to the archived view", async () => {
    put(item("n1", "needs_you"), item("f2", "failed"), item("d3", "done"));
    board();
    expect(await screen.findByText("2 NEED YOU")).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("link", { name: "completed and cancelled · auto-archive after 30 days" }));
    expect(where()).toBe("/archived");
  });

  it("filters by text, repo crumb and Chain menu, and groups and sorts from their menus, all in the URL", async () => {
    put(item("a1", "running", { repo: "/r/kraft-api" }), item("b2", "running", { repo: "/r/kraft-core", chain_template: "docs_only" }));
    board();
    const user = userEvent.setup();
    await user.type(await screen.findByRole("searchbox", { name: "Filter" }), "a1");
    expect(where()).toContain("q=a1");
    expect(screen.queryByText("Item b2")).toBeNull();
    await user.clear(screen.getByRole("searchbox", { name: "Filter" }));

    await user.click(screen.getByRole("button", { name: /all repos/ }));
    await user.click(screen.getByRole("menuitemradio", { name: /kraft-core/ }));
    expect(where()).toContain("repo=%2Fr%2Fkraft-core");
    expect(screen.queryByText("Item a1")).toBeNull();
    await user.click(within(screen.getByRole("banner")).getByRole("button", { name: /kraft-core/ }));
    await user.click(screen.getByRole("menuitemradio", { name: /All repos/ }));

    await user.click(screen.getByRole("button", { name: /^Chain/ }));
    await user.click(screen.getByRole("menuitemradio", { name: /docs_only/ }));
    expect(screen.getByRole("button", { name: /Chain · docs_only/ })).toBeInTheDocument();
    expect(screen.queryByText("Item a1")).toBeNull();

    await user.click(screen.getByRole("button", { name: /Group/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "Repo" }));
    expect(where()).toContain("group=repo");
    expect(screen.getByRole("region", { name: "kraft-core" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Sort/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "Title" }));
    expect(where()).toContain("sort=title");
  });

  it("starts Group from Appearance › Board", async () => {
    theme({ group_by: "template" });
    put(item("a1", "running"));
    board();
    expect(await screen.findByRole("region", { name: "default" })).toBeInTheDocument();
  });

  it("moves between rows with ↑/↓ across groups, and / focuses the filter", async () => {
    put(item("n1", "needs_you"), item("r2", "running"));
    board();
    const first = await screen.findByRole("button", { name: /Item n1/ });
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(screen.getByRole("button", { name: /Item r2/ })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "ArrowUp" });
    expect(first).toHaveFocus();
    fireEvent.keyDown(document.body, { key: "/" });
    expect(screen.getByRole("searchbox", { name: "Filter" })).toHaveFocus();
  });

  it("selects a row into the URL (peek), Escape clears it; with Open in: Full page it opens the item", async () => {
    put(item("r1", "running"));
    const { unmount } = board();
    await userEvent.click(await screen.findByRole("button", { name: /Item r1/ }));
    expect(where()).toBe("/?sel=r1");
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(where()).toBe("/");
    unmount();

    resetBoardPrefs();
    theme({ open_in: "full" });
    board();
    await act(async () => {});
    await userEvent.click(await screen.findByRole("button", { name: /Item r1/ }));
    expect(where()).toBe("/work-items/r1");
  });

  it("opens a gate row's item page on the gate, and resumes a paused row in place, its refusal on the row", async () => {
    put(item("g1", "needs_you", { stop: { kind: "gate", node: "plan_approval", reason: null, resume_at: null } as WorkItem["stop"], pending_gate: "plan_approval" }), item("p2", "paused"));
    const calls = stubFetch({ "POST /work-items/p2/resume": [409, { detail: "work item is active, not paused" }] });
    board();
    await userEvent.click(await screen.findByRole("button", { name: "Resume" }));
    expect(calls.find((c) => c.path === "/work-items/p2/resume")).toMatchObject({ method: "POST" });
    expect(await screen.findByRole("alert")).toHaveTextContent("work item is active, not paused");
    await userEvent.click(screen.getByRole("button", { name: "Review to approve" }));
    expect(where()).toBe("/work-items/g1?sel=plan_approval");
  });

  it("opens the composer instead of first-run when asked (FirstRun's last step)", async () => {
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [] });
    board("/?new=1");
    await act(async () => {});
    expect(screen.queryByRole("heading", { name: "Nothing on the board yet" })).toBeNull();
    expect(screen.getByRole("region", { name: "Needs you" })).toBeInTheDocument();
  });

  it("shows the skeleton while the first read runs with nothing loaded, and not once rows are there", async () => {
    let answer: (v: { items: WorkItem[]; cursor: number }) => void = () => {};
    vi.spyOn(api, "listWorkItems").mockReturnValue(new Promise((r) => (answer = r)));
    board();
    expect(await screen.findByLabelText("Loading the board")).toBeInTheDocument();
    expect(screen.queryByText(/NEED YOU/)).toBeNull();
    await act(async () => answer({ items: [item("n1", "needs_you")], cursor: 1 }));
    expect(screen.queryByLabelText("Loading the board")).toBeNull();
    expect(screen.getByText("Item n1")).toBeInTheDocument();
  });

  it("keeps the rows it has while a read runs, no skeleton over them", async () => {
    put(item("r1", "running"));
    vi.spyOn(api, "listWorkItems").mockReturnValue(new Promise(() => {}));
    board();
    expect(await screen.findByText("Item r1")).toBeInTheDocument();
    expect(screen.queryByLabelText("Loading the board")).toBeNull();
  });

  it("goes offline when the read fails: the banner carries the error, rows stay, actions are off; Retry now reads again", async () => {
    put(item("p1", "paused"));
    const list = vi.spyOn(api, "listWorkItems").mockRejectedValue(new Error("could not reach the Kraft server (GET /work-items) — it may have stopped."));
    board();
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("Could not load the board: could not reach the Kraft server (GET /work-items) — it may have stopped. Showing what was loaded before");
    expect(screen.getByText("OFFLINE")).toBeInTheDocument();
    expect(screen.getByText("Item p1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "+ New work item" })).toBeDisabled();
    list.mockResolvedValue({ items: [item("p1", "paused")], cursor: 1 });
    await userEvent.click(within(banner).getByRole("button", { name: "Retry now" }));
    expect(list).toHaveBeenCalledTimes(2);
    await act(async () => {});
    expect(screen.queryByText("OFFLINE")).toBeNull();
    expect(screen.getByRole("button", { name: "Resume" })).toBeEnabled();
  });

  it("is offline while the event socket reconnects, and reads again once it is back", async () => {
    put(item("p1", "paused"));
    board();
    await act(async () => {});
    act(() => useStore.setState({ connection: "reconnecting" } as never));
    expect(screen.getByText("OFFLINE")).toBeInTheDocument();
    const reads = vi.mocked(api.listWorkItems).mock.calls.length;
    act(() => useStore.setState({ connection: "open" } as never));
    await act(async () => {});
    expect(vi.mocked(api.listWorkItems).mock.calls.length).toBe(reads + 1);
    expect(screen.queryByText("OFFLINE")).toBeNull();
  });

  it("selects Done's rows with Select all, and keeps the ones a bulk action failed on checked", async () => {
    put(item("d1", "done", { status: "completed" }), item("d2", "done", { status: "completed" }), item("r3", "running"));
    stubFetch({ "POST /work-items/bulk": [200, { results: [{ id: "d1", ok: true }, { id: "d2", ok: false, error: "only a completed or abandoned item can be archived" }] }] });
    board();
    const done = await screen.findByRole("region", { name: "Done" });
    expect(within(screen.getByRole("region", { name: "Running" })).queryByRole("button", { name: "Select all" })).toBeNull();
    await userEvent.click(within(done).getByRole("button", { name: "Select all" }));
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Archive 2" }));
    expect(await screen.findByText("1 of 2 items archived")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Select Item d2" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Select Item d1" })).not.toBeChecked();
  });

  it("docks the peek for a selected row and remembers its width under kraft.ng.pane.board", async () => {
    put(item("r1", "running"));
    stubFetch({ "GET /work-items/r1": [200, item("r1", "running")], "GET /work-items/r1/events": [200, []] });
    localStorage.removeItem("kraft.ng.pane.board");
    board("/?sel=r1");
    const pane = await screen.findByRole("complementary", { name: "kraft-r1 pane" });
    const handle = within(pane).getByRole("separator", { name: "Resize pane" });
    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    // jsdom has no layout, so the width sits at the 300px floor; the key is what this pins.
    expect(localStorage.getItem("kraft.ng.pane.board")).toBe(handle.getAttribute("aria-valuenow"));
  });

  it("opens the peek on Config with the budget editor from a row's Raise budget", async () => {
    const b = item("b1", "needs_you", { status: "needs_human", stop: { kind: "budget", node: "verification", reason: "Spend cap reached", resume_at: null } as WorkItem["stop"], budget_cap: { cap_usd: 5, source: "policy", spent_usd: 5 } as WorkItem["budget_cap"] });
    put(b);
    stubFetch({ "GET /work-items/b1": [200, { ...b, worker_sessions: [] }], "GET /work-items/b1/events": [200, []], "GET /policy": [200, {}] });
    board();
    await userEvent.click(await screen.findByRole("button", { name: "Raise budget" }));
    expect(where()).toBe("/?sel=b1");
    expect(await screen.findByRole("tab", { name: "Config" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("textbox", { name: "Budget in dollars" })).toBeInTheDocument();
  });
});
