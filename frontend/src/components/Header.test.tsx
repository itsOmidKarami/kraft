import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import { Header } from "./Header";
import type { WorkItem } from "../types";

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

  it("shows repo › Board › id on an item page, with a ⋯ control instead of New", () => {
    useStore.setState({
      workItems: { wi_1: { id: "wi_1", repo: "/repo-a" } as WorkItem },
    } as never);
    renderAt("/work-items/wi_1");
    expect(screen.getByText("repo-a")).toBeInTheDocument();
    expect(screen.getByText("wi_1")).toBeInTheDocument();
    // "Board" is a real link here (Final's screen 11 draws it as `<a href>`),
    // unlike the plain-text "Board"/"Settings" on their own current pages.
    expect(screen.getByRole("link", { name: "Board" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("button", { name: /New work item/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "More" })).toBeInTheDocument();
  });

  it("shows Settings › <page> from settingsNav's label", () => {
    renderAt("/settings/chains");
    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(screen.getByText("Chains")).toBeInTheDocument();
  });

  it("shows the bare Analytics breadcrumb with no primary action", () => {
    renderAt("/analytics");
    expect(screen.getByText("Analytics")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /New work item/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "More" })).not.toBeInTheDocument();
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
