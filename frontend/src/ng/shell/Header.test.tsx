import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "../../store";
import { item } from "../../testFixtures";
import { Header } from "./Header";

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

  it("links Board back to the shipped board from a work item and carries full text in titles", () => {
    const it = item({ id: "w1", repo: "/r/very-long-repository-name", title: "A very long title" });
    useStore.setState({ workItems: { w1: it } });
    at("/work-items/w1");
    const nav = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(nav).getByRole("link", { name: "Board" })).toHaveAttribute("href", "/");
    expect(within(nav).getByText("very-long-repository-name")).toHaveAttribute("title", "/r/very-long-repository-name");
    expect(within(nav).getByText("A very long title")).toHaveAttribute("aria-current", "page");
  });

  it("has a slot for the page's actions that no crumb occupies", () => {
    at("/");
    expect(screen.getByRole("banner").querySelector(".ng-header-actions")).not.toBeNull();
  });
});
