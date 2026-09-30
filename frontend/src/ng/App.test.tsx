import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "./App";

afterEach(() => window.history.pushState({}, "", "/"));

describe("ng App", () => {
  it("renders the stub under the /ng basename, linking to the same page on the current UI", () => {
    window.history.pushState({}, "", "/ng/work-items/abc/review?x=1");
    render(<App />);
    expect(screen.getByRole("heading", { name: "Kraft next" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Current UI ↗" })).toHaveAttribute("href", "/work-items/abc?x=1#tab=changes");
  });

  it("renders nothing outside the basename", () => {
    window.history.pushState({}, "", "/work-items/abc");
    const { container } = render(<App />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the sign-in placeholder on a locked boot and on a later 401", () => {
    window.history.pushState({}, "", "/ng");
    const { unmount } = render(<App initiallyLocked />);
    expect(screen.getByRole("link", { name: "Sign in on the current UI" })).toHaveAttribute("href", "/");
    unmount();

    render(<App />);
    act(() => void window.dispatchEvent(new CustomEvent("kraft:unauthenticated")));
    expect(screen.queryByRole("heading", { name: "Kraft next" })).toBeNull();
    expect(screen.getByRole("link", { name: "Sign in on the current UI" })).toBeInTheDocument();
  });
});
