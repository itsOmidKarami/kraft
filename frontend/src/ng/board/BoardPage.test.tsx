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
import { resetBoardPrefs } from "./prefs";

const item = (id: string, display_status: DisplayStatus, over: Partial<WorkItem> = {}): WorkItem =>
  detail({ id, title: `Item ${id}`, display_status, bead_id: `kraft-${id}`, updated_at: `2026-09-13T0${id.slice(-1)}:00:00Z`, ...over });
const put = (...list: WorkItem[]) => useStore.setState({ workItems: Object.fromEntries(list.map((i) => [i.id, i])) } as never);

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
});
