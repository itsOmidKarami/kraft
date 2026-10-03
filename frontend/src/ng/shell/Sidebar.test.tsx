import { act, render, screen, within } from "@testing-library/react";
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

  it("collapses at once on unpin, with the pointer still on the pin, and keeps no focus that would hold it open", async () => {
    localStorage.setItem(SIDEBAR_KEY, "pinned");
    mount();
    const pin = screen.getByRole("button", { name: "Pin sidebar" });
    await userEvent.click(pin);
    expect(pin).toHaveAttribute("aria-pressed", "false");
    expect(document.querySelector(".ng-side")).toHaveAttribute("data-dismissed");
    expect(document.querySelector(".ng-sidebar")).not.toContainElement(document.activeElement as HTMLElement);
  });

  it("collapses on unpin from the keyboard too, keeping focus on the pin", async () => {
    localStorage.setItem(SIDEBAR_KEY, "pinned");
    mount();
    const pin = screen.getByRole("button", { name: "Pin sidebar" });
    pin.focus();
    await userEvent.keyboard("{Enter}");
    expect(pin).toHaveAttribute("aria-pressed", "false");
    expect(document.querySelector(".ng-side")).toHaveAttribute("data-dismissed");
    expect(pin).toHaveFocus();
  });

  it("keeps a revealed rail open under the pointer when a row is clicked, with no focus left to hold it open", async () => {
    localStorage.setItem(SIDEBAR_KEY, "rail");
    mount();
    const row = screen.getByRole("link", { name: "Analytics" });
    await userEvent.hover(row);
    await userEvent.click(row);
    expect(row).toHaveAttribute("aria-current", "page");
    expect(document.querySelector(".ng-side")).not.toHaveAttribute("data-dismissed");
    expect(screen.getByRole("main")).toHaveFocus();
  });

  it("closes a revealed rail when a row goes to a page from the keyboard", async () => {
    localStorage.setItem(SIDEBAR_KEY, "rail");
    mount();
    const row = screen.getByRole("link", { name: "Analytics" });
    row.focus();
    await userEvent.keyboard("{Enter}");
    expect(row).toHaveAttribute("aria-current", "page");
    expect(document.querySelector(".ng-side")).toHaveAttribute("data-dismissed");
    expect(screen.getByRole("main")).toHaveFocus();
  });

  it("closes a revealed rail when the window loses focus, without a click back in the page", async () => {
    localStorage.setItem(SIDEBAR_KEY, "rail");
    mount();
    await userEvent.tab();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Search" })).toHaveFocus();
    act(() => void window.dispatchEvent(new Event("blur")));
    expect(document.querySelector(".ng-side")).toHaveAttribute("data-dismissed");
    expect(document.querySelector(".ng-sidebar")).not.toContainElement(document.activeElement as HTMLElement);
  });

  it("reveals a closed rail again when the pointer comes back to it", async () => {
    localStorage.setItem(SIDEBAR_KEY, "rail");
    mount();
    act(() => void window.dispatchEvent(new Event("blur")));
    expect(document.querySelector(".ng-side")).toHaveAttribute("data-dismissed");
    await userEvent.hover(screen.getByRole("link", { name: "Analytics" }));
    expect(document.querySelector(".ng-side")).not.toHaveAttribute("data-dismissed");
  });

  it("does not move focus out of a pinned sidebar when a row is clicked", async () => {
    localStorage.setItem(SIDEBAR_KEY, "pinned");
    mount();
    const row = screen.getByRole("link", { name: "Analytics" });
    await userEvent.click(row);
    expect(row).toHaveFocus();
  });

  it("leaves Escape alone while pinned", async () => {
    localStorage.setItem(SIDEBAR_KEY, "pinned");
    mount();
    await userEvent.tab();
    await userEvent.tab();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Search" })).toHaveFocus();
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

  it("reads the footer from /health and links About", async () => {
    mount("/templates/chains");
    expect(await screen.findByRole("link", { name: "127.0.0.1:8765 · v0.9.4" })).toHaveAttribute("href", "/settings/about");
    expect(screen.queryByRole("link", { name: /Current UI/ })).toBeNull();
    expect(screen.queryByText(/restart to finish the update/)).toBeNull();
  });

  it("says to restart when the version on disk is not the one this server runs", async () => {
    vi.mocked(api.getHealth).mockResolvedValue({ ...HEALTH, installed: "0.9.5" } as never);
    mount("/templates/chains");
    expect(await screen.findByRole("link", { name: "v0.9.5 installed: restart to finish the update" })).toHaveAttribute("href", "/settings/about");
    expect(screen.getByRole("link", { name: "127.0.0.1:8765 · v0.9.4" })).toBeInTheDocument();
  });
});

describe("ng Sidebar, against a server older than its interface (R10c-01)", () => {
  it("says to restart when the server is too old to report what is installed", async () => {
    const { installed: _, ...old } = HEALTH;
    vi.mocked(api.getHealth).mockResolvedValue(old as never);
    mount("/templates/chains");
    expect(await screen.findByRole("link", { name: "a newer Kraft is installed: restart to finish the update" })).toHaveAttribute("href", "/settings/about");
  });
});

describe("ng Sidebar, with an older release installed (R10c-03)", () => {
  it("does not call a rollback an update to finish", async () => {
    vi.mocked(api.getHealth).mockResolvedValue({ ...HEALTH, installed: "0.9.3" } as never);
    mount("/templates/chains");
    expect(await screen.findByRole("link", { name: "v0.9.3 installed, older than this server: see About" })).toHaveAttribute("href", "/settings/about");
    expect(screen.queryByText(/restart to finish the update/)).toBeNull();
  });
});

describe("ng Sidebar draft dots", () => {
  const draft = (area: "chains" | "library" | "repos" | "policy" | "intake", key: string, problems: number) => ({ area, key, files: [], changes: 1, problems, updated_at: "" });

  it("marks an area with an open draft and counts its problems, from GET /drafts", async () => {
    vi.mocked(drafts.listDrafts).mockResolvedValue({ status: 200, body: [draft("chains", "default", 0), draft("chains", "broken", 2), draft("library", "library", 0)] });
    mount("/templates/chains/default");
    const chains = await within(nav()).findByRole("link", { name: "Chains, unpublished draft, 2 problems" });
    expect(chains).toHaveAttribute("aria-current", "page");
    expect(chains.querySelector(".ng-side-count")).toHaveTextContent("2");
    expect(chains.querySelector(".ng-side-mark.is-bad")).not.toBeNull();
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
