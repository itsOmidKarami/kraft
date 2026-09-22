import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import { Header } from "./Header";
import type { WorkItem } from "../types";
import { setPhoneWidth } from "../testFixtures";

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<Header onSearch={vi.fn()} onNew={vi.fn()} />} />
      </Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  useStore.setState({ workItems: {} } as never);
});

describe("Header", () => {
  it("shows the Board breadcrumb with its work-item/repo tally, and New work item", () => {
    const items: Record<string, WorkItem> = {
      a: { id: "a", repo: "/repo-a" } as WorkItem,
      b: { id: "b", repo: "/repo-b" } as WorkItem,
    };
    useStore.setState({ workItems: items } as never);
    renderAt("/");
    expect(screen.getByText("Board")).toBeInTheDocument();
    expect(screen.getByText(/2 work items across 2 repos/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /New work item/i })).toBeInTheDocument();
  });

  it("shows Board › title on an item page, with a ⋯ control instead of New", () => {
    useStore.setState({
      workItems: { wi_1: { id: "wi_1", repo: "/repo-a", title: "Fix the flaky import" } as WorkItem },
    } as never);
    renderAt("/work-items/wi_1");
    // The title, never the id; the repo is in the hero meta, not the crumb (W5.1).
    expect(screen.queryByText("repo-a")).not.toBeInTheDocument();
    expect(screen.getByText("Fix the flaky import")).toBeInTheDocument();
    expect(screen.queryByText("wi_1")).not.toBeInTheDocument();
    // "Board" is a real link here (Final's screen 11 draws it as `<a href>`),
    // unlike the plain-text "Board"/"Settings" on their own current pages.
    expect(screen.getByRole("link", { name: "Board" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("button", { name: /New work item/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "More actions" })).toBeInTheDocument();
  });

  it("cuts the item title crumb to one line with the whole title in its tooltip (W12.1)", () => {
    const title = "Design the caching layer for document search: embedding cache keyed by (repo, path, blob_sha)";
    useStore.setState({
      workItems: { wi_1: { id: "wi_1", repo: "/repo-a", title } as WorkItem },
    } as never);
    renderAt("/work-items/wi_1");
    const crumb = screen.getByText(title);
    expect(crumb).toHaveClass("app-header-crumb-current");
    expect(crumb).toHaveAttribute("title", title);
    expect(crumb).toHaveAttribute("data-allow-ellipsis");
    // the Board link before it is not cut
    expect(screen.getByRole("link", { name: "Board" })).not.toHaveAttribute("data-allow-ellipsis");
  });

  it("opens the item menu from the ⋯ control on an item page (W0.9)", async () => {
    useStore.setState({
      workItems: { wi_1: { id: "wi_1", repo: "/repo-a" } as WorkItem },
    } as never);
    renderAt("/work-items/wi_1");
    await userEvent.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual([
      "Archive",
      "Open worktree",
      "Copy id",
      "Copy link",
    ]);
  });

  it("shows Settings › <page> from settingsNav's label", () => {
    renderAt("/settings/chains");
    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(screen.getByText("Chains")).toBeInTheDocument();
  });

  it("hides on phone width on a settings sub-page (its own PhoneHeader is the one top bar), but not on the settings index", () => {
    setPhoneWidth();
    const { unmount } = renderAt("/settings/chains");
    expect(screen.queryByText("Chains")).toBeNull();
    unmount();
    renderAt("/settings");
    expect(screen.getByText("Settings")).toBeInTheDocument();
  });

  it("hides on phone width on an item page, where PhoneTopBar is the one header (W3.1)", () => {
    setPhoneWidth();
    useStore.setState({
      workItems: { wi_1: { id: "wi_1", repo: "/repo-a", title: "Fix the flaky import" } as WorkItem },
    } as never);
    const { container } = renderAt("/work-items/wi_1");
    expect(container.querySelector(".app-header")).toBeNull();
  });

  it("shows Board › Archived · N item(s) on the archived route, singular for one (Kraft-h7igq)", async () => {
    vi.spyOn(api, "listArchivedWorkItems").mockResolvedValue({
      items: [{ id: "a" } as never],
      cursor: 1,
    });
    renderAt("/archived");
    expect(await screen.findByText("Archived · 1 item")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Board" })).toHaveAttribute("href", "/");
  });

  it("shows the bare Analytics breadcrumb with no primary action", () => {
    renderAt("/analytics");
    expect(screen.getByText("Analytics")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /New work item/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "More actions" })).not.toBeInTheDocument();
  });

  it("disables New work item and shows '0 work items · no repos' with zero repos", async () => {
    vi.spyOn(api, "getRepos").mockResolvedValue({ repos: [] });
    renderAt("/");
    expect(await screen.findByText(/no repos/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new work item/i })).toBeDisabled();
  });

  it("calls onSearch from the Search button", async () => {
    const onSearch = vi.fn();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Header onSearch={onSearch} onNew={vi.fn()} />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("button", { name: /search/i }));
    expect(onSearch).toHaveBeenCalledOnce();
  });
});
