import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "../App";
import { legacyPath } from "../legacyPath";
import { HeaderActions } from "./HeaderActions";
import { ROUTES } from "./routes";
import { Shell } from "./Shell";

afterEach(() => window.history.pushState({}, "", "/"));

const inShell = (page: JSX.Element) =>
  render(
    <MemoryRouter>
      <Routes>
        <Route element={<Shell />}>
          <Route path="*" element={page} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );

describe("ng Shell", () => {
  it("puts a skip link first in the tab order and moves focus to main", async () => {
    inShell(<p>page</p>);
    await userEvent.tab();
    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("main")).toHaveFocus();
    expect(screen.getByRole("main")).toHaveAttribute("id", "ng-main");
  });

  it("shows a page's HeaderActions in the header, not in the page", () => {
    inShell(<HeaderActions><button>Save</button></HeaderActions>);
    expect(within(screen.getByRole("banner")).getByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(within(screen.getByRole("main")).queryByRole("button", { name: "Save" })).toBeNull();
  });

  it.each(ROUTES.filter((r) => !r.built))(
    "$path renders its placeholder, linking to the same page on the current UI",
    (r) => {
      window.history.pushState({}, "", `/ng${r.path === "/" ? "" : r.path}`);
      render(<App />);
      expect(screen.getByRole("heading", { name: r.label })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Open it on the current UI ↗" })).toHaveAttribute(
        "href",
        legacyPath({ pathname: `/ng${r.path}`, search: "" }),
      );
    },
  );

  it("renders an item path and an unknown path as placeholders without throwing", () => {
    window.history.pushState({}, "", "/ng/work-items/abc/review?x=1");
    const { unmount } = render(<App />);
    expect(screen.getByRole("heading", { name: "Work item" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open it on the current UI ↗" })).toHaveAttribute("href", "/work-items/abc?x=1#tab=changes");
    unmount();
    window.history.pushState({}, "", "/ng/nope/at/all");
    render(<App />);
    expect(screen.getByRole("heading", { name: "Not found" })).toBeInTheDocument();
  });
});
