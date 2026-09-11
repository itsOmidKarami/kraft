import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "../store";
import type { WorkItem } from "../types";
import { BottomNav } from "./BottomNav";

beforeEach(() => {
  useStore.setState({ workItems: {} } as never);
});

describe("BottomNav", () => {
  it("renders one tab per top-level screen, Board matching only the root", () => {
    render(
      <MemoryRouter initialEntries={["/work-items/w1"]}>
        <BottomNav />
      </MemoryRouter>,
    );
    const nav = screen.getByRole("navigation", { name: "primary" });
    const tabs = ["Board", "Search", "Analytics", "Settings"];
    for (const label of tabs) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
    // On a work item's detail route, the Board tab must not read as active —
    // NavLink's default (non-`end`) match would otherwise treat "/" as a
    // prefix of every route and light Board up everywhere.
    expect(within(nav).getByRole("link", { name: "Board" })).not.toHaveClass("active");
  });

  it("shows a needs-you count badge on the Board tab only", () => {
    useStore.setState({
      workItems: {
        w1: { id: "w1", status: "needs_human", pending_gate: "plan_approval" } as WorkItem,
      },
    } as never);
    render(
      <MemoryRouter>
        <BottomNav />
      </MemoryRouter>,
    );
    const boardTab = screen.getByRole("link", { name: /board/i });
    expect(within(boardTab).getByText("1")).toBeInTheDocument();
    expect(
      within(screen.getByRole("link", { name: /search/i })).queryByText("1"),
    ).toBeNull();
  });
});
