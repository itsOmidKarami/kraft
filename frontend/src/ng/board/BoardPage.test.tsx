import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import type { DisplayStatus, WorkItem } from "../../types";
import { detail, stubFetch } from "../item/testkit";
import { usePaneMemory } from "../item/Workspace";
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
  localStorage.clear();
});

describe("BoardPage, against a server older than its interface (R10c-01)", () => {
  // Over first-run too: it sat behind the wizard (R11a-11).
  it.each([["the board", true], ["first-run", false]])("says over %s to restart when /health has a version but no installed", async (_where, items) => {
    vi.mocked(api.getHealth).mockResolvedValue({ status: "ok", version: "1.4.0" } as never);
    if (items) put(item("w1", "running"));
    else vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [] });
    board();
    if (!items) expect(await screen.findByRole("heading", { name: "Nothing on the board yet" })).toBeInTheDocument();
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("This server is older than its web interface");
    expect(banner).toHaveTextContent("Run kraft admin restart.");
  });

  it("says nothing when the server reports what is installed", async () => {
    vi.mocked(api.getHealth).mockResolvedValue({ status: "ok", version: "2.0.0", installed: "2.0.0" } as never);
    put(item("w1", "running"));
    board();
    await screen.findByText("Item w1");
    await waitFor(() => expect(api.getHealth).toHaveBeenCalled());
    expect(screen.queryByText(/older than its web interface/)).toBeNull();
  });
});

describe("BoardPage", () => {
  it("shows first-run only when no repo is connected", async () => {
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [] });
    board();
    expect(await screen.findByRole("heading", { name: "Nothing on the board yet" })).toBeInTheDocument();
  });

  it("brings back a first-run left part-way while its repo is connected, and starts over once no repo is", async () => {
    localStorage.setItem("kraft.firstRun", JSON.stringify({ step: 3, reached: 3, path: "/r", name: "r", disabled: false }));
    vi.spyOn(api, "getTemplates").mockResolvedValue([]);
    const { unmount } = board();
    expect(await screen.findByRole("heading", { name: /register Kraft with Claude Code/ })).toBeInTheDocument();
    unmount();
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [] });
    board();
    expect(await screen.findByRole("heading", { name: "Connect a repo" })).toBeInTheDocument();
    expect(localStorage.getItem("kraft.firstRun")).toBeNull();
  });

  // R11a-02: items filed from the CLI ran and finished while the board kept saying "Nothing on the board yet".
  it.each([["already there", true], ["filed while it shows", false]])("gives first-run up for the board once an item exists: %s", async (_when, before) => {
    localStorage.setItem("kraft.firstRun", JSON.stringify({ step: 3, reached: 3, path: "/r", name: "r", disabled: false }));
    vi.spyOn(api, "getTemplates").mockResolvedValue([]);
    if (before) put(item("w1", "done"));
    board();
    if (!before) {
      expect(await screen.findByRole("heading", { name: /register Kraft with Claude Code/ })).toBeInTheDocument();
      act(() => useStore.setState({ workItems: { w1: item("w1", "done") } }));
    }
    expect(await screen.findByText("Item w1")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /register Kraft with Claude Code/ })).toBeNull();
    expect(localStorage.getItem("kraft.firstRun")).toBeNull();
  });

  it("drops a kept first-run whose repo is no longer connected, and keeps the board", async () => {
    localStorage.setItem("kraft.firstRun", JSON.stringify({ step: 3, reached: 3, path: "/elsewhere/other", name: "other", disabled: false }));
    board();
    await act(async () => {});
    expect(screen.queryByRole("heading", { name: "Nothing on the board yet" })).toBeNull();
    expect(screen.getByRole("region", { name: "Needs you" })).toBeInTheDocument();
    expect(localStorage.getItem("kraft.firstRun")).toBeNull();
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
    expect(document.querySelector(".board-row .ticks")).not.toBeNull();
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
    usePaneMemory.setState({ pane: { open: false, userCollapsed: true } });
    await userEvent.click(screen.getByRole("button", { name: "Review to approve" }));
    expect(where()).toBe("/work-items/g1?sel=plan_approval");
    expect(usePaneMemory.getState().pane).toEqual({ open: true, userCollapsed: false });
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

  it("lays the peek over the list, which keeps its width and its rows' tick strips, and remembers the peek's width under kraft.ng.pane.board", async () => {
    put(item("r1", "running"));
    stubFetch({ "GET /work-items/r1": [200, item("r1", "running")], "GET /work-items/r1/events": [200, []] });
    localStorage.removeItem("kraft.ng.pane.board");
    board();
    await screen.findByRole("button", { name: /Item r1/ });
    const list = document.querySelector<HTMLElement>(".board-list")!;
    const before = list.getAttribute("style");
    await userEvent.click(screen.getByRole("button", { name: /Item r1/ }));
    const pane = await screen.findByRole("complementary", { name: "Item r1 pane" });
    expect(list.getAttribute("style")).toBe(before);
    expect(document.querySelectorAll(".board-row .ticks")).toHaveLength(1);
    const handle = within(pane).getByRole("separator", { name: "Resize pane" });
    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    // jsdom has no layout, so the width sits at the 300px floor; the key is what this pins.
    expect(localStorage.getItem("kraft.ng.pane.board")).toBe(handle.getAttribute("aria-valuenow"));
  });

  it("closes the peek on a press anywhere outside it, the header and sidebar included, and not on a press inside it or on a row", async () => {
    put(item("r1", "running"), item("r2", "running"));
    stubFetch({ "GET /work-items/r1": [200, item("r1", "running")], "GET /work-items/r2": [200, item("r2", "running")], "GET /work-items/r1/events": [200, []], "GET /work-items/r2/events": [200, []] });
    board("/?sel=r1");
    const pane = await screen.findByRole("complementary", { name: "Item r1 pane" });
    fireEvent.pointerDown(within(pane).getByRole("tab", { name: "Activity" }));
    expect(where()).toBe("/?sel=r1");
    await userEvent.click(screen.getByRole("checkbox", { name: "Select Item r2" }));
    expect(where()).toBe("/?sel=r1");
    await userEvent.click(screen.getByRole("button", { name: /Item r2/ }));
    expect(where()).toBe("/?sel=r2");
    fireEvent.pointerDown(document.querySelector(".ng-header")!);
    expect(where()).toBe("/");
    await userEvent.click(screen.getByRole("button", { name: /Item r1/ }));
    expect(where()).toBe("/?sel=r1");
    fireEvent.pointerDown(screen.getByRole("complementary", { name: "Sidebar" }));
    expect(where()).toBe("/");
    await userEvent.click(screen.getByRole("button", { name: /Item r1/ }));
    fireEvent.pointerDown(document.querySelector(".board-list")!);
    expect(where()).toBe("/");
  });

  it("keeps the peek on a right-click outside it, and on a press in a popover, dialog or toast, or on the list's scrollbar", async () => {
    put(item("r1", "running"));
    stubFetch({ "GET /work-items/r1": [200, item("r1", "running")], "GET /work-items/r1/events": [200, []] });
    board("/?sel=r1");
    await screen.findByRole("complementary", { name: "Item r1 pane" });
    fireEvent.pointerDown(document.querySelector(".ng-header")!, { button: 2 });
    expect(where()).toBe("/?sel=r1");
    // What a popover, dialog or toast portals into the body, as ui/ renders them.
    for (const cls of ["popover", "dialog-backdrop", "toasts"]) {
      const layer = document.body.appendChild(document.createElement("div"));
      layer.className = cls;
      const inside = layer.appendChild(document.createElement("button"));
      fireEvent.pointerDown(inside);
      layer.remove();
      expect(where()).toBe("/?sel=r1");
    }
    const list = document.querySelector<HTMLElement>(".board-list")!;
    Object.defineProperty(list, "clientWidth", { value: 600, configurable: true });
    const onBar = new MouseEvent("pointerdown", { bubbles: true, button: 0 });
    Object.defineProperty(onBar, "offsetX", { value: 605 });
    act(() => void list.dispatchEvent(onBar));
    expect(where()).toBe("/?sel=r1");
    fireEvent.pointerDown(list);
    expect(where()).toBe("/");
  });

  it("leaves Escape in a dialog opened from the peek to the dialog, the peek and what was typed staying", async () => {
    const failed = item("f1", "failed", { status: "needs_human", stop: { kind: "failed", node: "verification", task: null, reason: "The forge refused.", resume_at: null } as WorkItem["stop"] });
    put(failed);
    stubFetch({ "GET /work-items/f1": [200, { ...failed, worker_sessions: [] }], "GET /work-items/f1/events": [200, []] });
    board("/?sel=f1");
    const pane = await screen.findByRole("complementary", { name: "Item f1 pane" });
    await userEvent.click(await within(pane).findByRole("button", { name: "Escalate…" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Message" }), "Look at the lint step");
    await userEvent.keyboard("{Escape}");
    expect(where()).toBe("/?sel=f1");
    expect(screen.getByRole("complementary", { name: "Item f1 pane" })).toBeInTheDocument();
  });

  it("hands focus back to the row when Escape closes the peek with focus on the page", async () => {
    put(item("r1", "running"));
    stubFetch({ "GET /work-items/r1": [200, item("r1", "running")], "GET /work-items/r1/events": [200, []] });
    board("/?sel=r1");
    await screen.findByRole("complementary", { name: "Item r1 pane" });
    screen.getByRole("main").focus();
    await userEvent.keyboard("{Escape}");
    expect(where()).toBe("/");
    await waitFor(() => expect(screen.getByRole("button", { name: /Item r1/ })).toHaveFocus());
  });

  it("closes the peek completely from its collapse button and from Escape inside it, leaving no rail, focus back on the row", async () => {
    put(item("r1", "running"));
    stubFetch({ "GET /work-items/r1": [200, item("r1", "running")], "GET /work-items/r1/events": [200, []] });
    board("/?sel=r1");
    const pane = await screen.findByRole("complementary", { name: "Item r1 pane" });
    await userEvent.click(within(pane).getByRole("button", { name: "Collapse pane" }));
    expect(where()).toBe("/");
    expect(screen.queryByRole("complementary", { name: /pane/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Expand pane" })).toBeNull();
    await waitFor(() => expect(screen.getByRole("button", { name: /Item r1/ })).toHaveFocus());
    await userEvent.click(screen.getByRole("button", { name: /Item r1/ }));
    const again = await screen.findByRole("complementary", { name: "Item r1 pane" });
    within(again).getByRole("tab", { name: "Overview" }).focus();
    await userEvent.keyboard("{Escape}");
    expect(where()).toBe("/");
    expect(screen.queryByRole("button", { name: "Expand pane" })).toBeNull();
  });

  it("opens a budget stop's peek on Overview, whose Raise cap opens Config with the budget editor", async () => {
    const b = item("b1", "needs_you", { status: "needs_human", stop: { kind: "budget", node: "verification", reason: "Spend cap reached", resume_at: null, scope: "work_item" } as WorkItem["stop"], budget_cap: { cap_usd: 5, source: "policy", spent_usd: 5 } as WorkItem["budget_cap"] });
    put(b);
    stubFetch({ "GET /work-items/b1": [200, { ...b, worker_sessions: [] }], "GET /work-items/b1/events": [200, []], "GET /policy": [200, {}] });
    board();
    expect(screen.queryByRole("button", { name: "Raise budget" })).toBeNull();
    await userEvent.click(await screen.findByRole("button", { name: "Open" }));
    expect(where()).toBe("/?sel=b1");
    expect(await screen.findByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    // The banner's Raise cap; the footer's main button says the same and does the same.
    await userEvent.click((await screen.findAllByRole("button", { name: "Raise cap" }))[0]);
    expect(await screen.findByRole("tab", { name: "Config" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("textbox", { name: "Budget in dollars" })).toBeInTheDocument();
  });

  it("opens the composer from + New work item, closing the peek", async () => {
    put(item("r1", "running"));
    vi.spyOn(api, "getTemplates").mockResolvedValue([]);
    stubFetch({ "GET /work-items/r1": [200, item("r1", "running")] });
    board("/?sel=r1");
    await userEvent.click(await screen.findByRole("button", { name: "+ New work item" }));
    expect(where()).toBe("/?new=1");
    expect(screen.getByRole("region", { name: "New work item" })).toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: /pane/ })).toBeNull();
  });
});
