import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";

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
afterEach(() => window.history.pushState({}, "", "/"));

describe("the shipped addresses that moved, at phone width", () => {
  it("lands an old address on the screen that replaced it", async () => {
    width(true);
    window.history.pushState({}, "", "/ng/settings/notify?x=1");
    render(<App />);
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe("/ng/settings/notifications?x=1"));
  });

  it("opens the review for a shipped #tab=changes address", async () => {
    width(true);
    window.history.pushState({}, "", "/ng/work-items/abc#tab=changes");
    render(<App />);
    await waitFor(() => expect(window.location.pathname).toBe("/ng/work-items/abc/review"));
  });

  it("does not send /search to the board: the phone has a Search screen", () => {
    width(true);
    window.history.pushState({}, "", "/ng/search");
    render(<App />);
    expect(window.location.pathname).toBe("/ng/search");
    expect(screen.getByRole("heading", { level: 1, name: "Search" })).toBeInTheDocument();
  });
});

describe("ng App at phone width (A.1)", () => {
  it("renders the phone app with the tab bar, not the desktop shell", () => {
    width(true);
    window.history.pushState({}, "", "/ng/search");
    render(<App />);
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Skip to content" })).toBeNull();
  });

  it("renders the desktop shell above 767px", () => {
    width(false);
    window.history.pushState({}, "", "/ng");
    render(<App />);
    expect(screen.getByRole("link", { name: "Skip to content" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Primary" })).toBeNull();
  });

  it("swaps shapes on a resize and keeps the address", () => {
    const set = width(true);
    window.history.pushState({}, "", "/ng/analytics");
    render(<App />);
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    act(() => set(false));
    expect(window.location.pathname).toBe("/ng/analytics");
    expect(screen.queryByRole("navigation", { name: "Primary" })).toBeNull();
    expect(screen.getByRole("link", { name: "Skip to content" })).toBeInTheDocument();
  });

  it("still shows the sign-in card when locked, at phone width too", () => {
    width(true);
    window.history.pushState({}, "", "/ng");
    render(<App initiallyLocked />);
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Primary" })).toBeNull();
  });
});
