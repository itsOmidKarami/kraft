import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import * as api from "../api";
import * as session from "./session";
import { stubFetch } from "./item/testkit";
import { storageUsage } from "./settings/testkit";

vi.mock("./session", () => ({ resumeSession: vi.fn(async () => {}), startEvents: vi.fn() }));

afterEach(() => {
  window.history.pushState({}, "", "/");
  vi.unstubAllGlobals();
});

describe("ng App", () => {
  it("renders the board at the root", () => {
    window.history.pushState({}, "", "/");
    render(<App />);
    expect(screen.getByRole("heading", { level: 1, name: "Board" })).toBeInTheDocument();
  });

  it("shows the sign-in card on a locked boot and on a later 401, not the shell", () => {
    window.history.pushState({}, "", "/");
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
    window.history.pushState({}, "", "/settings/harnesses");
    render(<App initiallyLocked />);
    await userEvent.type(screen.getByLabelText(/^Password/), "pw{Enter}");
    expect(await screen.findByRole("link", { name: /Harnesses/ })).toHaveAttribute("aria-current", "page");
    expect(window.location.pathname).toBe("/settings/harnesses");
    expect(session.resumeSession).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it("routes /work-items/:id/review to the review page", async () => {
    vi.spyOn(api, "getWorkItem").mockResolvedValue({ id: "abc", title: "Cache embeddings", repo: "/r/x", worker_sessions: [], chain_definition: { nodes: [] } } as never);
    window.history.pushState({}, "", "/work-items/abc/review");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Review changes: Cache embeddings" })).toBeInTheDocument();
    vi.restoreAllMocks();
  });

  it("routes /settings/storage to the Storage page, not the placeholder", async () => {
    stubFetch({ "GET /storage": [200, storageUsage()] });
    window.history.pushState({}, "", "/settings/storage");
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "Storage" })).toBeInTheDocument();
    expect(screen.queryByText("There is no page at this address.")).toBeNull();
  });
});

describe("the dev pages, /_tokens and /_gallery", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it.each([["/_tokens", "Tokens"], ["/_gallery", "Graph components"]])("the dev server routes %s", async (path, title) => {
    window.history.pushState({}, "", path);
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: title }, { timeout: 5000 })).toBeInTheDocument();
  });

  it.each([["/_tokens", "Tokens"], ["/_gallery", "Graph components"]])("a release build has no %s", async (path, title) => {
    vi.stubEnv("DEV", false);
    vi.stubEnv("VITE_DEV_PAGES", "");
    vi.resetModules();
    const { App: Release } = await import("./App");
    window.history.pushState({}, "", path);
    render(<Release />);
    expect(await screen.findByRole("heading", { level: 1, name: "Not found" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: title })).toBeNull();
  });
});
