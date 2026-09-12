import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import { AppNav } from "./AppNav";
import type { WorkItem } from "../types";

const ITEM: WorkItem = {
  id: "wi_1",
  title: "t",
  repo: "/repo-a",
  status: "needs_human",
  pending_gate: "human_review",
  chain_template: "default",
  chain_definition: { template_id: "default", nodes: [] },
  current_node_id: "verify",
  bead_id: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppNav />
    </MemoryRouter>,
  );

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  useStore.setState({ workItems: {}, connection: "open" } as never);
  vi.spyOn(api, "getHealth").mockResolvedValue({
    status: "ok",
    invalid_templates: {},
    invalid_policy: [],
    bind: "127.0.0.1",
    port: 8765,
    version: "0.9.4",
  });
});

describe("AppNav", () => {
  it("shows the needs-you count on the Board row from deriveState, not raw status", async () => {
    useStore.setState({ workItems: { [ITEM.id]: ITEM } } as never);
    renderAt("/");
    expect(await screen.findByText("1")).toBeInTheDocument();
  });

  it("collapses to the rail on toggle and persists it across a remount", async () => {
    renderAt("/");
    expect(screen.getByRole("link", { name: /Board/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Collapse/ }));
    expect(localStorage.getItem("kraft.sidebar_collapsed")).toBe("true");
    expect(screen.queryByText("Kraft")).not.toBeInTheDocument();

    const { unmount } = render(
      <MemoryRouter initialEntries={["/"]}>
        <AppNav />
      </MemoryRouter>,
    );
    expect(screen.getAllByRole("button", { name: /Expand/ })[0]).toBeInTheDocument();
    unmount();
  });

  it("defaults to the rail under 1280 when the user has not chosen", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as never);
    renderAt("/");
    expect(document.querySelector(".app-rail")).not.toBeNull();
  });

  it("an explicit expanded choice beats the narrow default", () => {
    localStorage.setItem("kraft.sidebar_collapsed", "false");
    vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as never);
    renderAt("/");
    expect(document.querySelector(".app-sidebar")).not.toBeNull();
  });

  it("shows the item's repo initial as the rail avatar on an item page, not the K default", async () => {
    localStorage.setItem("kraft.sidebar_collapsed", "true");
    useStore.setState({ workItems: { [ITEM.id]: ITEM } } as never);
    render(
      <MemoryRouter initialEntries={[`/work-items/${ITEM.id}`]}>
        <AppNav />
      </MemoryRouter>,
    );
    // repoName("/repo-a") is "repo-a" (the full last path segment); the rail
    // avatar's first letter is "r", not the "-a" suffix that distinguishes
    // this fixture from the "/repo-b" used elsewhere in the test suite.
    expect(await screen.findByText("r")).toBeInTheDocument();
  });

  it("expands Settings in place, listing both groups", async () => {
    renderAt("/settings/repos");
    expect(await screen.findByText("How work runs")).toBeInTheDocument();
    expect(screen.getByText("This instance")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Chains/ })).toBeInTheDocument();
  });

  it("shows the degraded health flag in the footer", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue({
      status: "degraded",
      invalid_templates: { broken: "broken.yaml: bad hook" },
      invalid_policy: [],
      bind: "127.0.0.1",
      port: 8765,
      version: "0.9.4",
    });
    renderAt("/");
    expect(await screen.findByText(/broken/)).toBeInTheDocument();
    expect(await screen.findByText(/127\.0\.0\.1:8765/)).toBeInTheDocument();
    expect(await screen.findByText(/v0\.9\.4/)).toBeInTheDocument();
  });
});
