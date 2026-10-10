import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import type { WorkItem } from "../../types";
import { ROUTES } from "./routes";
import { Shell } from "./Shell";
import { SIDEBAR_KEY } from "./sidebarPref";
import * as drafts from "../templates/draft/draftApi";
import * as http from "../http";
import { countIn } from "../board/counts";

const ITEM: WorkItem = {
  id: "wi_1",
  title: "t",
  repo: "/repo-a",
  status: "needs_human",
  pending_gate: "human_review",
  display_status: "needs_you",
  chain_template: "default",
  chain_definition: { template_id: "default", nodes: [] },
  current_node_id: "verify",
  bead_id: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
const HEALTH = { status: "ok", invalid_templates: {}, invalid_policy: [], bind: "127.0.0.1", port: 8765, version: "0.9.4", installed: "0.9.4" };

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
  vi.spyOn(drafts, "listDrafts").mockResolvedValue({ status: 200, body: [] });
  vi.spyOn(http, "request").mockResolvedValue({ status: 200, body: { installed: "0.9.4", latest: "v0.9.4", channel: "stable", behind: false, checked_at: null } });
  useStore.setState({ workItems: {}, sessionsByItem: {}, eventsByItem: {}, connection: "open" } as never);
});

const nav = () => screen.getByRole("navigation", { name: "Pages" });

describe("ng Sidebar", () => {
  it("lists the pages in order, with the right hrefs, group heads and an aria-current row", async () => {
    mount("/templates/library");
    const rows = within(nav()).getAllByRole("link");
    expect(rows.map((r) => r.getAttribute("aria-label"))).toEqual(ROUTES.filter((r) => r.group).map((r) => r.label));
    expect(rows.map((r) => r.getAttribute("href"))).toEqual(ROUTES.filter((r) => r.group).map((r) => r.path));
    // About closes the Settings group (SB-5): bind:port and the restart warnings live there.
    expect(rows.at(-1)).toHaveAttribute("href", "/settings/about");
    // Storage sits just above About, and a click lands on its page.
    expect(rows.at(-2)).toHaveAttribute("href", "/settings/storage");
    expect(rows.at(-2)).toHaveAttribute("aria-label", "Storage");
    expect(within(nav()).getByRole("button", { name: "Search" })).toBeInTheDocument();
    expect(within(nav()).getByText("Templates")).toBeInTheDocument();
    expect(within(nav()).getByText("Settings")).toBeInTheDocument();
    expect(within(nav()).getByRole("link", { name: "Library" })).toHaveAttribute("aria-current", "page");
    expect(within(nav()).getByRole("link", { name: "Chains" })).not.toHaveAttribute("aria-current");
    await screen.findByRole("link", { name: "Kraft v0.9.4" });
  });

  it("prints Search's shortcut for the platform: Ctrl+K off a Mac, ⌘K on one", async () => {
    vi.spyOn(navigator, "platform", "get").mockReturnValue("Linux x86_64");
    const { unmount } = mount();
    expect(screen.getByRole("button", { name: /^Search/ })).toHaveTextContent("Ctrl+K");
    expect(screen.queryByText("⌘K")).toBeNull();
    unmount();
    vi.spyOn(navigator, "platform", "get").mockReturnValue("MacIntel");
    mount();
    expect(screen.getByRole("button", { name: /^Search/ })).toHaveTextContent("⌘K");
  });

  it("keeps every row in the tab order, so an unpinned sidebar works without a pointer", async () => {
    localStorage.setItem(SIDEBAR_KEY, "rail");
    mount();
    await userEvent.tab(); // skip link
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Pin sidebar" })).toHaveFocus();
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

  it("puts the pin in the head beside Kraft · live, its panel icon and title following the state", async () => {
    localStorage.setItem(SIDEBAR_KEY, "pinned");
    mount();
    const pin = screen.getByRole("button", { name: "Pin sidebar" });
    expect(pin.closest(".ng-side-head")).not.toBeNull();
    expect(pin).toHaveAttribute("data-tip", "Collapse sidebar");
    expect(pin.querySelector(".lucide-panel-left-close")).not.toBeNull();
    await userEvent.click(pin);
    expect(pin).toHaveAttribute("data-tip", "Pin sidebar");
    expect(pin.querySelector(".lucide-panel-left-open")).not.toBeNull();
  });

  it("starts from the stored choice", () => {
    localStorage.setItem(SIDEBAR_KEY, "pinned");
    mount();
    expect(screen.getByRole("button", { name: "Pin sidebar" })).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps one name for the pin, its state in aria-pressed alone, so it never reads \"Collapse sidebar, pressed\"", async () => {
    localStorage.setItem(SIDEBAR_KEY, "pinned");
    mount();
    const pin = screen.getByRole("button", { name: "Pin sidebar" });
    expect(pin).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(pin);
    expect(pin).toHaveAccessibleName("Pin sidebar");
    expect(pin).toHaveAttribute("aria-pressed", "false");
  });

  it("falls back to pinned, without an error, when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    vi.stubGlobal("innerWidth", 1024);
    expect(() => mount()).not.toThrow();
    expect(screen.getByRole("button", { name: "Pin sidebar" })).toHaveAttribute("aria-pressed", "true");
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

  // Unpinned, only the pointer leaving (or focus leaving) hides it, in CSS. A
  // close in script would also move focus out of it, which is what these catch.
  it.each<[string, () => Promise<void>, ("pinned" | "rail")?]>([
    ["unpinning with the pin", async () => void (await userEvent.click(screen.getByRole("button", { name: "Pin sidebar" }))), "pinned"],
    ["unpinning with Ctrl+\\", async () => { screen.getByRole("link", { name: "Analytics" }).focus(); await userEvent.keyboard("{Control>}\\{/Control}"); }, "pinned"],
    ["the window losing focus", async () => { screen.getByRole("link", { name: "Analytics" }).focus(); act(() => void window.dispatchEvent(new Event("blur"))); }],
    ["choosing a row", async () => void (await userEvent.click(screen.getByRole("link", { name: "Analytics" })))],
  ])("leaves an unpinned sidebar open after %s, keeping focus in it", async (_, act_, from = "rail") => {
    localStorage.setItem(SIDEBAR_KEY, from);
    mount();
    await act_();
    expect(screen.getByRole("button", { name: "Pin sidebar" })).toHaveAttribute("aria-pressed", "false");
    expect(document.querySelector(".ng-side")!.attributes).toHaveLength(1);
    expect(document.querySelector(".ng-sidebar")).toContainElement(document.activeElement as HTMLElement);
  });

  // #502 review (WCAG 1.4.13): shown by focus, an unpinned sidebar could not be dismissed from the
  // keyboard, and after Enter on a row it stayed over the new page.
  it.each<[string, () => Promise<void>]>([
    ["Escape", async () => { screen.getByRole("link", { name: "Analytics" }).focus(); await userEvent.keyboard("{Escape}"); }],
    ["choosing a row from the keyboard", async () => { screen.getByRole("link", { name: "Analytics" }).focus(); await userEvent.keyboard("{Enter}"); }],
  ])("hands focus to the page after %s on an unpinned sidebar, which hides it", async (_, act_) => {
    localStorage.setItem(SIDEBAR_KEY, "rail");
    mount();
    await act_();
    await waitFor(() => expect(screen.getByRole("main")).toHaveFocus());
  });

  it("does not move focus out of a pinned sidebar when a row is clicked", async () => {
    localStorage.setItem(SIDEBAR_KEY, "pinned");
    mount();
    const row = screen.getByRole("link", { name: "Analytics" });
    await userEvent.click(row);
    expect(row).toHaveFocus();
  });

  it("counts what the board's Needs you group holds, a paused mid-chain item included and a never-started one not", () => {
    const midChain = { ...ITEM, id: "wi_5", status: "paused" as const, display_status: "paused" as const, current_node_id: "implementation" };
    const fresh = { ...ITEM, id: "wi_6", status: "paused" as const, display_status: "paused" as const, current_node_id: null };
    const items = { [ITEM.id]: ITEM, wi_5: midChain, wi_6: fresh };
    useStore.setState({ workItems: items } as never);
    mount();
    expect(countIn(Object.values(items) as never, "needs")).toBe(2);
    expect(screen.getByRole("link", { name: "Board, 2 need you" })).toBeInTheDocument();
  });

  it("marks the Board when an item needs you or failed, from the server's display_status", () => {
    const failed = { ...ITEM, id: "wi_2", display_status: "failed" as const };
    const running = { ...ITEM, id: "wi_3", status: "active" as const, display_status: "running" as const };
    useStore.setState({ workItems: { [ITEM.id]: ITEM, wi_2: failed, wi_3: running } } as never);
    mount();
    expect(screen.getByRole("link", { name: "Board, 2 need you" })).toBeInTheDocument();
    act(() => useStore.setState({ workItems: { wi_3: running, wi_4: { ...ITEM, id: "wi_4", display_status: "escalated" } } } as never));
    expect(screen.getByRole("link", { name: "Board" })).toBeInTheDocument();
    act(() => useStore.setState({ workItems: { [ITEM.id]: ITEM } } as never));
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

  it("shows the version alone in the footer, linking About", async () => {
    mount("/templates/chains");
    const foot = await screen.findByRole("link", { name: "Kraft v0.9.4" });
    expect(foot).toHaveAttribute("href", "/settings/about");
    expect(foot).toHaveTextContent(/^v0\.9\.4$/);
    expect(screen.queryByText(/127\.0\.0\.1|restart|installed/)).toBeNull();
  });

  // A restart is what an installed update waits on, and the name says so (#502 review).
  const AVAILABLE = "Kraft v0.9.4, update available", RESTART = "Kraft v0.9.4, restart to finish the update";
  // The visible word says it too, not only the name (R13b-05).
  it.each<[string, object, boolean, string, string]>([
    ["the feed has a newer release", HEALTH, true, AVAILABLE, "update"],
    ["a newer release is installed and waits on a restart", { ...HEALTH, installed: "0.9.5" }, false, RESTART, "restart"],
    ["the server is too old to report what is installed (R10c-01)", (({ installed: _, ...h }) => h)(HEALTH), false, RESTART, "restart"],
    ["an older release is installed (R10c-03)", { ...HEALTH, installed: "0.9.3" }, false, RESTART, "restart"],
  ])("marks an update in the footer when %s", async (_, health, behind, name, word) => {
    vi.mocked(api.getHealth).mockResolvedValue(health as never);
    vi.mocked(http.request).mockResolvedValue({ status: 200, body: { installed: "0.9.4", latest: "v0.9.9", channel: "stable", behind, checked_at: null } });
    mount("/templates/chains");
    const foot = await screen.findByRole("link", { name });
    expect(foot).toHaveAttribute("href", "/settings/about");
    expect(foot).toHaveTextContent(`v0.9.4${word}`);
  });
});

describe("ng Sidebar draft dots", () => {  const draft = (area: "chains" | "library" | "repos" | "policy" | "intake", key: string, problems: number) => ({ area, key, files: [], changes: 1, problems, updated_at: "" });

  it("marks an area with an open draft and counts its problems, from GET /drafts", async () => {
    vi.mocked(drafts.listDrafts).mockResolvedValue({ status: 200, body: [draft("chains", "default", 0), draft("chains", "broken", 2), draft("library", "library", 0)] });
    mount("/templates/chains/default");
    const chains = await within(nav()).findByRole("link", { name: "Chains, unpublished draft, 2 problems" });
    expect(chains).toHaveAttribute("aria-current", "page");
    expect(chains.querySelector(".ng-side-count")).toHaveTextContent("2");
    const library = within(nav()).getByRole("link", { name: "Library, unpublished draft" });
    expect(library.querySelector(".ng-side-count")).toBeNull();
    expect(within(nav()).getByRole("link", { name: "Harnesses" }).querySelector(".ng-side-dot")).toBeNull();
  });

  it("marks the Repos, Policy and Auto-intake rows from their own draft areas, and Policy stays active on a section page", async () => {
    vi.mocked(drafts.listDrafts).mockResolvedValue({ status: 200, body: [draft("repos", "repos", 1), draft("policy", "policy", 0), draft("intake", "intake", 0)] });
    mount("/settings/policy/loops");
    const policy = await within(nav()).findByRole("link", { name: "Policy, unpublished draft" });
    expect(policy).toHaveAttribute("aria-current", "page");
    expect(within(nav()).getByRole("link", { name: "Repos, unpublished draft, 1 problem" })).toBeInTheDocument();
    expect(within(nav()).getByRole("link", { name: "Auto-intake, unpublished draft" })).toBeInTheDocument();
    expect(within(nav()).getByRole("link", { name: "Harnesses" }).querySelector(".ng-side-dot")).toBeNull();
  });

  it("reads the drafts again when one changes", async () => {
    mount();
    await within(nav()).findByRole("link", { name: "Chains" });
    vi.mocked(drafts.listDrafts).mockResolvedValue({ status: 200, body: [draft("chains", "default", 1)] });
    act(() => drafts.draftsChanged());
    await within(nav()).findByRole("link", { name: "Chains, unpublished draft, 1 problem" });
  });
});
