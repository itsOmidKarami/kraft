import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import type { DisplayStatus, WorkItem } from "../../types";
import { detail } from "../item/testkit";
import { BoardPage } from "./BoardPage";
import { resetBoardPrefs } from "./prefs";

const item = (id: string, display_status: DisplayStatus, over: Partial<WorkItem> = {}): WorkItem =>
  detail({ id, title: `Item ${id}`, display_status, updated_at: `2026-09-13T0${id.slice(-1)}:00:00Z`, ...over });

const board = (path = "/") => render(<MemoryRouter initialEntries={[path]}><BoardPage /></MemoryRouter>);

beforeEach(() => {
  resetBoardPrefs();
  vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [{ path: "/r" } as never] });
  vi.spyOn(api, "getTheme").mockResolvedValue({ board: { group_by: "status", show_done: 2, open_in: "peek" } } as never);
});
afterEach(() => vi.restoreAllMocks());

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
    useStore.setState({ workItems: Object.fromEntries([item("n1", "needs_you"), item("r1", "running"), item("p1", "paused", { current_node_id: null }), ...["d1", "d2", "d3"].map((d) => item(d, "done"))].map((i) => [i.id, i])) } as never);
    board();
    const done = await screen.findByRole("region", { name: "Done" });
    expect(within(screen.getByRole("region", { name: "Needs you" })).getByText("Item n1")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Not started" })).getByText("Item p1")).toBeInTheDocument();
    await act(async () => {});
    expect(within(done).queryByText("Item d1")).toBeNull();
    await userEvent.click(within(done).getByRole("button", { name: "show all 3" }));
    expect(within(done).getByText("Item d1")).toBeInTheDocument();
  });
});
