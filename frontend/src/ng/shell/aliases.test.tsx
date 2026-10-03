import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { App } from "../App";
import { SCREENS } from "../phone/screens";
import { aliasTarget, shippedHash } from "./aliases";

vi.mock("../session", () => ({ resumeSession: vi.fn(async () => {}), startEvents: vi.fn() }));

afterEach(() => {
  window.history.pushState({}, "", "/");
  vi.restoreAllMocks();
});

// Every address the shipped UI answered, copied from its router at e58957e2c
// (App.tsx routes, settingsNav.ts pages, views/settings/index.tsx aliases),
// because the cutover deletes those files. Each must land on a built page.
const SHIPPED: [from: string, lands: string][] = [
  ["/", "/"],
  ["/archived", "/archived"],
  ["/analytics", "/analytics"],
  ["/search", "/"],
  ["/work-items/abc", "/work-items/abc"],
  ["/settings", "/settings/policy/limits"],
  ["/templates/repos", "/settings/repos"],
  ["/settings/chains", "/templates/chains"],
  ["/settings/library", "/templates/library"],
  ["/templates/harnesses", "/settings/harnesses"],
  ["/settings/policy", "/settings/policy/limits"],
  ["/settings/intake", "/settings/auto-intake"],
  ["/settings/notify", "/settings/notifications"],
  ["/settings/access", "/settings/access"],
  ["/settings/appearance", "/settings/appearance"],
  ["/settings/templates", "/templates/chains"],
  ["/settings/plugins", "/templates/chains"],
  ["/settings/steering", "/templates/library"],
];

describe("shipped addresses", () => {
  it.each(SHIPPED)("%s lands on %s, never on Not found", async (from, lands) => {
    vi.spyOn(api, "getWorkItem").mockResolvedValue({ id: "abc", title: "Cache embeddings", repo: "/r/x", worker_sessions: [], chain_definition: { nodes: [] } } as never);
    window.history.pushState({}, "", from);
    render(<App />);
    await waitFor(() => expect(window.location.pathname.replace(/\/$/, "")).toBe(lands.replace(/\/$/, "")));
    expect(screen.queryByRole("heading", { name: "Not found" })).toBeNull();
  });

  it("keeps the query string and adds no history entry", async () => {
    window.history.pushState({}, "", "/settings/chains?chain=default");
    const depth = window.history.length;
    render(<App />);
    await waitFor(() => expect(window.location.pathname).toBe("/templates/chains"));
    expect(window.location.search).toBe("?chain=default");
    expect(window.history.length).toBe(depth);
  });

  it("turns a shipped item hash into the page it pointed at", async () => {
    vi.spyOn(api, "getWorkItem").mockResolvedValue({ id: "abc", title: "Cache embeddings", repo: "/r/x", worker_sessions: [], chain_definition: { nodes: [] } } as never);
    window.history.pushState({}, "", "/work-items/abc#node=verify&tab=tasks");
    const depth = window.history.length;
    render(<App />);
    await waitFor(() => expect(window.location.pathname).toBe("/work-items/abc/nodes/verify"));
    expect(window.location.hash).toBe("");
    expect(window.history.length).toBe(depth);
  });
});

// Every phone screen the desktop has no page for, and the page it lands on
// when the window widens past 767px or a link sent from a phone opens on a
// laptop: the two shapes share one address space (R3-04).
const PHONE: [from: string, lands: string][] = [
  ["/more", "/"],
  ["/more?x=1", "/?x=1"],
  ["/settings/harnesses/claude", "/settings/harnesses?harness=claude"],
  ["/settings/harnesses/profiles/deep", "/settings/harnesses?profile=deep"],
  ["/settings/harnesses/profiles/deep?yaml=1", "/settings/harnesses?profile=deep&yaml=1"],
  ["/settings/notifications/webhook", "/settings/notifications"],
  ["/settings/auto-intake/schedules/0", "/settings/auto-intake"],
];

describe("phone addresses at desktop width", () => {
  it.each(PHONE)("%s lands on %s, never on Not found", async (from, lands) => {
    window.history.pushState({}, "", from);
    render(<App />);
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe(lands));
    expect(screen.queryByRole("heading", { name: "Not found" })).toBeNull();
  });

  // The guard for the next phone screen: its address must answer here too.
  it.each([...new Set(SCREENS.map((s) => s.route))])("the phone's %s is a desktop page as well", async (route) => {
    vi.spyOn(api, "getWorkItem").mockResolvedValue({ id: "abc", title: "Cache embeddings", repo: "/r/x", worker_sessions: [], chain_definition: { nodes: [] } } as never);
    window.history.pushState({}, "", route.replace(/:[^/]+/g, "abc"));
    render(<App />);
    expect(screen.queryByRole("heading", { name: "Not found" })).toBeNull();
  });
});

describe("aliasTarget", () => {
  it.each([
    ["/settings/harnesses?harness=:id", { id: "claude" }, "", "/settings/harnesses?harness=claude"],
    ["/settings/harnesses?harness=:id", { id: "a b/c" }, "", "/settings/harnesses?harness=a%20b%2Fc"],
    ["/settings/harnesses?harness=:id", { id: "x" }, "?lane=y", "/settings/harnesses?harness=x&lane=y"],
    ["/settings/notifications", { channel: "webhook" }, "?x=1", "/settings/notifications?x=1"],
    ["/", {}, "?", "/"],
  ])("%s with %o and %s → %s", (to, params, search, out) => {
    expect(aliasTarget(to, params, search)).toBe(out);
  });
});

describe("shippedHash", () => {
  it.each([
    ["/work-items/abc", "#node=verify", "/work-items/abc/nodes/verify"],
    ["/work-items/abc", "#node=verify&tab=changes&file=a.py", "/work-items/abc/review"],
    ["/work-items/abc", "#tab=changes", "/work-items/abc/review"],
    ["/work-items/abc", "#tab=documents&doc=d1", "/work-items/abc"],
    ["/work-items/abc", "#session=s1&max=1", "/work-items/abc"],
    ["/work-items/abc", "", null],
    ["/work-items/abc", "#", null],
    ["/work-items/abc", "#summary", null],
    ["/work-items/abc", "#section=notes", null],
    ["/work-items/abc/review", "#node=x", null],
    ["/", "#node=x", null],
  ])("%s%s → %s", (path, hash, to) => {
    expect(shippedHash(path, hash)).toBe(to);
  });
});
