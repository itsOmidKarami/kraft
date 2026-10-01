import { act, render, screen } from "@testing-library/react";
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
