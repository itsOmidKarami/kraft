import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { ROUTES } from "../shell/routes";
import { stubFetch } from "../item/testkit";

vi.mock("../session", () => ({ resumeSession: vi.fn(async () => {}), startEvents: vi.fn() }));

function width(phone: boolean) {
  const listeners = new Set<() => void>();
  const mq = { matches: phone, addEventListener: (_: string, l: () => void) => listeners.add(l), removeEventListener: (_: string, l: () => void) => listeners.delete(l) };
  vi.stubGlobal("matchMedia", () => mq);
  return (p: boolean) => {
    mq.matches = p;
    listeners.forEach((l) => l());
  };
}
afterEach(() => {
  window.history.pushState({}, "", "/");
  vi.unstubAllGlobals();
});
const notFound = () => screen.queryAllByRole("heading", { name: "Not found" });

describe("the shipped addresses that moved, at phone width", () => {
  it("lands an old address on the screen that replaced it", async () => {
    width(true);
    window.history.pushState({}, "", "/settings/notify?x=1");
    render(<App />);
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe("/settings/notifications?x=1"));
  });

  it("opens the review for a shipped #tab=changes address", async () => {
    width(true);
    window.history.pushState({}, "", "/work-items/abc#tab=changes");
    render(<App />);
    await waitFor(() => expect(window.location.pathname).toBe("/work-items/abc/review"));
  });

  it("does not send /search to the board: the phone has a Search screen", () => {
    width(true);
    window.history.pushState({}, "", "/search");
    render(<App />);
    expect(window.location.pathname).toBe("/search");
    expect(screen.getByRole("heading", { level: 1, name: "Search" })).toBeInTheDocument();
  });
});

describe("ng App at phone width (A.1)", () => {
  it("renders the phone app with the tab bar, not the desktop shell", () => {
    width(true);
    window.history.pushState({}, "", "/search");
    render(<App />);
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Skip to content" })).toBeNull();
  });

  it("renders the desktop shell above 767px", () => {
    width(false);
    window.history.pushState({}, "", "/");
    render(<App />);
    expect(screen.getByRole("link", { name: "Skip to content" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Primary" })).toBeNull();
  });

  it("swaps shapes on a resize and keeps the address", () => {
    const set = width(true);
    window.history.pushState({}, "", "/analytics");
    render(<App />);
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    act(() => set(false));
    expect(window.location.pathname).toBe("/analytics");
    expect(screen.queryByRole("navigation", { name: "Primary" })).toBeNull();
    expect(screen.getByRole("link", { name: "Skip to content" })).toBeInTheDocument();
  });

  it("still shows the sign-in card when locked, at phone width too", () => {
    width(true);
    window.history.pushState({}, "", "/");
    render(<App initiallyLocked />);
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Primary" })).toBeNull();
  });
});

// The two shapes share one address space: a desktop page's address opens a
// phone screen, and a phone screen's lands on a desktop page when the window
// widens past 767px. Neither side ever shows Not found (R3-04).
describe("one address space across a resize", () => {
  it.each([...ROUTES.map((r) => r.path), "/settings/intake", "/settings/repos/kraft", "/templates/library/implementation", "/settings/policy/loops"])("the desktop's %s opens a phone screen", (path) => {
    width(true);
    stubFetch();
    window.history.pushState({}, "", path);
    render(<App />);
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(notFound()).toEqual([]);
  });

  it("opens the archived list at phone width", async () => {
    width(true);
    stubFetch({ "GET /work-items": [200, { items: [{ id: "w1", title: "Cache embeddings", repo: "/code/kraft", archived_at: "2026-09-30T08:00:00Z" }], cursor: 0 }] });
    window.history.pushState({}, "", "/archived");
    render(<App />);
    expect(screen.getByRole("heading", { level: 1, name: "Archived" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /Cache embeddings/ })).toHaveAttribute("href", "/work-items/w1");
  });

  it("sends the desktop's Storage address to More: the phone has no Storage screen", async () => {
    width(true);
    stubFetch();
    window.history.pushState({}, "", "/settings/storage");
    render(<App />);
    await waitFor(() => expect(window.location.pathname).toBe("/more"));
    expect(notFound()).toEqual([]);
  });

  it("lands the desktop's /settings/intake on Auto-intake", async () => {
    width(true);
    stubFetch();
    window.history.pushState({}, "", "/settings/intake?x=1");
    render(<App />);
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe("/settings/auto-intake?x=1"));
  });

  it.each([
    ["/more", "/"],
    ["/settings/harnesses/claude", "/settings/harnesses?harness=claude"],
    ["/settings/harnesses/profiles/deep", "/settings/harnesses?profile=deep"],
    ["/settings/notifications/webhook", "/settings/notifications"],
    ["/settings/auto-intake/schedules/0", "/settings/auto-intake"],
    ["/archived", "/archived"],
  ])("widening on %s lands on the desktop's %s", async (from, lands) => {
    const set = width(true);
    window.history.pushState({}, "", from);
    render(<App />);
    expect(notFound()).toEqual([]);
    act(() => set(false));
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe(lands));
    expect(screen.getByRole("link", { name: "Skip to content" })).toBeInTheDocument();
    expect(notFound()).toEqual([]);
  });

  it.each([
    ["/settings/harnesses?harness=claude", "/settings/harnesses/claude"],
    ["/settings/harnesses?profile=deep", "/settings/harnesses/profiles/deep"],
    ["/settings/harnesses?harness=claude&lane=y", "/settings/harnesses/claude?lane=y"],
    ["/settings/harnesses?yaml=1&profile=deep", "/settings/harnesses/profiles/deep?yaml=1"],
  ])("narrowing on %s opens the phone's %s", async (from, lands) => {
    const set = width(false);
    window.history.pushState({}, "", from);
    render(<App />);
    act(() => set(true));
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe(lands));
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
  });
});
