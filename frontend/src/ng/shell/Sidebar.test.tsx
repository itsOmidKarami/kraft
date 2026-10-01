import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import type { WorkItem } from "../../types";
import { legacyPath } from "../legacyPath";
import { ROUTES } from "./routes";
import { Shell } from "./Shell";
import { SIDEBAR_KEY } from "./sidebarPref";

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
const HEALTH = { status: "ok", invalid_templates: {}, invalid_policy: [], bind: "127.0.0.1", port: 8765, version: "0.9.4" };

const mount = (path = "/") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Shell />
    </MemoryRouter>,
  );

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.sidebar;
  vi.restoreAllMocks();
  vi.spyOn(api, "getHealth").mockResolvedValue(HEALTH as never);
  useStore.setState({ workItems: {}, sessionsByItem: {}, eventsByItem: {}, connection: "open" } as never);
});

const nav = () => screen.getByRole("navigation", { name: "Pages" });

describe("ng Sidebar", () => {
  it("lists the pages in order, with the right hrefs, group heads and an aria-current row", async () => {
    mount("/templates/library");
    const rows = within(nav()).getAllByRole("link");
    expect(rows.map((r) => r.getAttribute("aria-label"))).toEqual(ROUTES.filter((r) => r.group).map((r) => r.label));
    expect(rows.map((r) => r.getAttribute("href"))).toEqual(ROUTES.filter((r) => r.group).map((r) => r.path));
    expect(within(nav()).getByRole("button", { name: "Search" })).toBeInTheDocument();
    expect(within(nav()).getByText("Templates")).toBeInTheDocument();
    expect(within(nav()).getByText("Settings")).toBeInTheDocument();
    expect(within(nav()).getByRole("link", { name: "Library" })).toHaveAttribute("aria-current", "page");
    expect(within(nav()).getByRole("link", { name: "Chains" })).not.toHaveAttribute("aria-current");
    await screen.findByText("127.0.0.1:8765 · v0.9.4");
  });

  it("keeps every row in the tab order, so the rail works without a pointer", async () => {
    mount();
    await userEvent.tab(); // skip link
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Search" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("link", { name: "Board" })).toHaveFocus();
  });

  it("toggles the pin on the button, storing it and setting the attribute", async () => {
    mount();
    const pin = screen.getByRole("button", { name: /sidebar$/ });
    const was = pin.getAttribute("aria-pressed");
    await userEvent.click(pin);
    const now = pin.getAttribute("aria-pressed");
    expect(now).not.toBe(was);
    expect(localStorage.getItem(SIDEBAR_KEY)).toBe(now === "true" ? "pinned" : "rail");
    expect(document.documentElement.dataset.sidebar).toBe(now === "true" ? "pinned" : "rail");
  });

  it("starts from the stored choice", () => {
    localStorage.setItem(SIDEBAR_KEY, "pinned");
    mount();
    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toHaveAttribute("aria-pressed", "true");
  });

  it("falls back to the width default, without an error, when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    vi.stubGlobal("innerWidth", 1024);
    expect(() => mount()).not.toThrow();
    expect(screen.getByRole("button", { name: "Pin sidebar" })).toHaveAttribute("aria-pressed", "false");
    vi.unstubAllGlobals();
  });

  it("toggles on Ctrl+\\ and Cmd+\\ but not from a text field", async () => {
    localStorage.setItem(SIDEBAR_KEY, "rail");
    mount();
    await userEvent.keyboard("{Control>}\\{/Control}");
    expect(localStorage.getItem(SIDEBAR_KEY)).toBe("pinned");
    await userEvent.keyboard("{Meta>}\\{/Meta}");
    expect(localStorage.getItem(SIDEBAR_KEY)).toBe("rail");
    const field = document.createElement("input");
    document.body.append(field);
    field.focus();
    await userEvent.keyboard("{Control>}\\{/Control}");
    expect(localStorage.getItem(SIDEBAR_KEY)).toBe("rail");
    field.remove();
  });

  it("closes a revealed rail on Escape and returns focus to main", async () => {
    localStorage.setItem(SIDEBAR_KEY, "rail");
    mount();
    await userEvent.tab();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Search" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("main")).toHaveFocus();
    expect(document.querySelector(".ng-side")).toHaveAttribute("data-dismissed");
  });

  it("leaves Escape alone while pinned", async () => {
    localStorage.setItem(SIDEBAR_KEY, "pinned");
    mount();
    await userEvent.tab();
    await userEvent.tab();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Search" })).toHaveFocus();
  });

  it("marks the Board when an item needs you, from deriveState", () => {
    useStore.setState({ workItems: { [ITEM.id]: ITEM } } as never);
    mount();
    expect(screen.getByRole("link", { name: "Board, 1 need you" })).toBeInTheDocument();
    act(() => useStore.setState({ workItems: {} } as never));
    expect(screen.getByRole("link", { name: "Board" })).toBeInTheDocument();
  });

  it.each([
    ["open", "live"],
    ["connecting", "connecting…"],
    ["reconnecting", "reconnecting…"],
  ])("says %s as %s", (connection, word) => {
    useStore.setState({ connection } as never);
    mount();
    expect(screen.getByText(word)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: word })).toBeInTheDocument();
  });

  it("reads the footer from /health and links About and the current UI", async () => {
    mount("/templates/chains");
    expect(await screen.findByRole("link", { name: "127.0.0.1:8765 · v0.9.4" })).toHaveAttribute("href", "/settings/about");
    expect(screen.getByRole("link", { name: "Current UI ↗" })).toHaveAttribute(
      "href",
      legacyPath({ pathname: "/ng/templates/chains", search: "" }),
    );
  });
});
