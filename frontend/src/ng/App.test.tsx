import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import * as session from "./session";

vi.mock("./session", () => ({ resumeSession: vi.fn(async () => {}), startEvents: vi.fn() }));

afterEach(() => window.history.pushState({}, "", "/"));

describe("ng App", () => {
  it("renders under the /ng basename, the board at its root", () => {
    window.history.pushState({}, "", "/ng");
    render(<App />);
    expect(screen.getByRole("region", { name: "Needs you" })).toBeInTheDocument();
  });

  it("renders nothing outside the basename", () => {
    window.history.pushState({}, "", "/work-items/abc");
    const { container } = render(<App />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the sign-in card on a locked boot and on a later 401, not the shell", () => {
    window.history.pushState({}, "", "/ng");
    const { unmount } = render(<App initiallyLocked />);
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Sign in on the current UI/ })).toBeNull();
    unmount();

    render(<App />);
    act(() => void window.dispatchEvent(new CustomEvent("kraft:unauthenticated")));
    expect(screen.queryByRole("heading", { name: "Board" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("signs in, runs the boot probe once, then shows the shell where the person was", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(String(url).endsWith("/health") ? { status: "ok" } : { ok: true }), { status: 200 })));
    window.history.pushState({}, "", "/ng/templates/chains");
    render(<App initiallyLocked />);
    await userEvent.type(screen.getByLabelText(/^Password/), "pw{Enter}");
    expect(await screen.findByRole("heading", { name: "Chains" })).toBeInTheDocument();
    expect(session.resumeSession).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });
});
