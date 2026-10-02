import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "../../store";
import { item } from "../../testFixtures";
import { Header } from "./Header";
import { HeaderActions, HeaderTail } from "./HeaderActions";
import { Shell } from "./Shell";
import { usePageItem } from "./pageItem";

const at = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Header actionsRef={() => {}} />
    </MemoryRouter>,
  );

beforeEach(() => useStore.setState({ workItems: {} }));

describe("Header", () => {
  it("marks only the last crumb as the current page and leaves it unlinked", () => {
    at("/settings/policy");
    const nav = screen.getByRole("navigation", { name: "Breadcrumb" });
    const current = within(nav).getByText("Policy");
    expect(current).toHaveAttribute("aria-current", "page");
    expect(within(nav).queryByRole("link")).toBeNull();
    expect(within(nav).getByText("Settings")).not.toHaveAttribute("aria-current");
  });

  it("links Board back to the board, and the repo to the board filtered to it, from a work item, and carries full text in titles", () => {
    const it = item({ id: "w1", repo: "/r/very-long-repository-name", title: "A very long title", bead_id: "kraft-cb59" });
    useStore.setState({ workItems: { w1: it } });
    at("/work-items/w1");
    const nav = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(nav).getByRole("link", { name: "Board" })).toHaveAttribute("href", "/");
    const repo = within(nav).getByRole("link", { name: "very-long-repository-name" });
    expect(repo).toHaveAttribute("href", "/?repo=%2Fr%2Fvery-long-repository-name");
    expect(repo.closest("li")).toHaveAttribute("title", "/r/very-long-repository-name");
    expect(within(nav).getByText("kraft-cb59")).toHaveAttribute("aria-current", "page");
  });

  it("takes the item page's own item when the board list lacks it, and opens its MR in a new tab", () => {
    usePageItem.setState({ item: { id: "w9", repo: "/r/kraft", title: "t", bead_id: "kraft-x1", mr_ref: { number: 7, url: "https://forge/7" }, display_status: "running" } });
    at("/work-items/w9");
    const nav = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(nav).getByText("kraft-x1")).toHaveAttribute("aria-current", "page");
    const mr = within(nav).getByRole("link", { name: "!7 ↗" });
    expect(mr).toHaveAttribute("target", "_blank");
    expect(mr).toHaveAttribute("rel", "noopener noreferrer");
    usePageItem.setState({ item: null });
  });

  it("has a slot for the page's actions that no crumb occupies", () => {
    at("/");
    expect(screen.getByRole("banner").querySelector(".ng-header-actions")).not.toBeNull();
  });

  it("puts a page's HeaderTail right after the crumbs, before its actions", () => {
    const Page = () => <><HeaderTail><span>all repos</span></HeaderTail><HeaderActions><button type="button">New</button></HeaderActions></>;
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes><Route element={<Shell />}><Route path="*" element={<Page />} /></Route></Routes>
      </MemoryRouter>,
    );
    const banner = screen.getByRole("banner");
    const parts = [...banner.children].map((c) => c.className);
    expect(parts).toEqual(["ng-crumbs-nav", "ng-header-tail", "ng-header-actions"]);
    expect(banner.querySelector(".ng-header-tail")).toHaveTextContent("all repos");
  });

  it("links Chains on a chain's page, where the page's tail goes on after it", () => {
    let tail: HTMLDivElement | null = null;
    render(
      <MemoryRouter initialEntries={["/templates/chains/default"]}>
        <Header actionsRef={() => {}} tailRef={(el) => void (tail = el)} />
      </MemoryRouter>,
    );
    const nav = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(nav).getByRole("link", { name: "Chains" })).toHaveAttribute("href", "/templates/chains");
    expect(within(nav).queryByText("default")).toBeNull();
    expect(tail).not.toBeNull();
    expect(nav.nextElementSibling).toBe(tail);
  });
});
