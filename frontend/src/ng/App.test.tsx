import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "./App";

afterEach(() => window.history.pushState({}, "", "/"));

describe("ng App", () => {
  it("renders under the /ng basename, the Board stub at its root", () => {
    window.history.pushState({}, "", "/ng");
    render(<App />);
    expect(screen.getByRole("heading", { name: "Board" })).toBeInTheDocument();
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
    expect(screen.queryByRole("heading", { name: "Board" })).toBeNull();
    expect(screen.getByRole("link", { name: "Sign in on the current UI" })).toBeInTheDocument();
  });
});
