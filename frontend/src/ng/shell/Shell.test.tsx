import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import { App } from "../App";
import { HeaderActions } from "./HeaderActions";
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

  it("says it is offline over any page, with the read's error, and Retry now reads again", async () => {
    const list = vi.spyOn(api, "listWorkItems").mockResolvedValue({ items: [], cursor: 1 });
    useStore.setState({ loadError: "could not reach the Kraft server (GET /work-items?x=1) — it may have stopped.", connection: "open" } as never);
    inShell(<p>page</p>);
    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent("Offline: could not reach the Kraft server — it may have stopped. What is shown may be out of date");
    await userEvent.click(within(banner).getByRole("button", { name: "Retry now" }));
    expect(list).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    act(() => useStore.setState({ connection: "reconnecting" } as never));
    expect(screen.getByRole("alert")).toHaveTextContent("Offline: the live connection dropped.");
    act(() => useStore.setState({ connection: "open" } as never));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  // The review route was a placeholder until W8 built it (App.test.tsx covers it now).
  it("renders an unknown path as the not-found placeholder without throwing", () => {
    window.history.pushState({}, "", "/nope/at/all");
    render(<App />);
    expect(screen.getByRole("heading", { name: "Not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the board" })).toHaveAttribute("href", "/");
  });

  it("opens the board's composer from N on another page", async () => {
    window.history.pushState({}, "", "/settings/about");
    render(<App />);
    await userEvent.keyboard("N");
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe("/?new=1"));
  });

  it("offers the section above an unknown address inside Settings", () => {
    window.history.pushState({}, "", "/settings/nope");
    render(<App />);
    expect(screen.getByRole("link", { name: "Up to Policy" })).toHaveAttribute("href", "/settings/policy");
  });
});

describe("ng Shell chrome", () => {
  it("sets the tab title from the route", () => {
    inShell(<p>page</p>);
    expect(document.title).toBe("Board · Kraft");
  });

  it("moves focus to the new page's heading on a route change, not into an open field", async () => {
    render(
      <MemoryRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route path="*" element={<><h1>Page</h1><input aria-label="Box" /></>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("link", { name: "Analytics" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Page" })).toHaveFocus());
    screen.getByRole("textbox", { name: "Box" }).focus();
    fireEvent.click(screen.getByRole("link", { name: "Chains" }));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByRole("heading", { name: "Page" })).not.toHaveFocus();
  });

  it("opens the one shortcut help from ? on any page, but not from a field or over a dialog", async () => {
    inShell(<><h1>Anything</h1><input aria-label="Box" /></>);
    await userEvent.type(screen.getByRole("textbox", { name: "Box" }), "?");
    expect(screen.queryByRole("dialog")).toBeNull();
    await userEvent.keyboard("{Escape}");
    screen.getByRole("textbox", { name: "Box" }).blur();
    await userEvent.keyboard("?");
    const help = screen.getByRole("dialog", { name: "Keyboard shortcuts" });
    expect(help).toHaveTextContent("Search");
    expect(help).toHaveTextContent("New work item");
    await userEvent.keyboard("N");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });
});
