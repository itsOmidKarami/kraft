import { act, render, screen, waitFor, within } from "@testing-library/react";
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
});
