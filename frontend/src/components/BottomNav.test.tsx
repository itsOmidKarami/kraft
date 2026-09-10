import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { BottomNav } from "./BottomNav";

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
});
