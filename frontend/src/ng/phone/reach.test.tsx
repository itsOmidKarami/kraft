import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubFetch } from "../item/testkit";
import { parentOf } from "./nav/route";
import { PhoneApp } from "./PhoneApp";
import { SCREENS, type Tap } from "./screens";

/** What the screens read before any item exists: each route's own empty shape. */
const EMPTY: Record<string, [number, unknown]> = {
  "GET /work-items": [200, []],
  "GET /repos": [200, { repos: [{ path: "/code/kraft", name: "kraft" }] }],
  "GET /apply": [200, { restart: [], reload: [], managed: false }],
  "GET /drafts": [200, []],
  "GET /templates/chains": [200, []],
  "GET /analytics": [200, { totals: { work_items: 0, work_items_run: 0, by_status: {}, mrs_merged: 0, wall_ms: 0, human_wait_ms: 0, tokens_in: 0, tokens_out: 0, cost_usd: 0, cost_complete: true, rounds: 0, capped_out: 0, completed: 0, completed_prev: null, median_lead_ms: 0, human_wait_pct: 0, fix_cycles: 0, fix_cycles_capped: 0, rejected_gates: 0, unplanned_touches_per_item: 0, open_mr_to_green_ci_ms: 0 }, by_node: [], by_repo: [], by_template: [], daily: [], findings: [] }],
  "GET /notify": [200, { enabled: false, url_set: false, base_url: null, events: [], last_test: null }],
  "GET /access": [200, { bind: "127.0.0.1", port: 8765, session_expiry_days: 7, password_set: false, auth_required: false, allowed_hosts: [] }],
  "GET /sessions": [200, { sessions: [] }],
  "GET /theme": [200, { palette: "nocturne", mode: "dark", density: "compact", board: { group_by: "status", show_done: 5, open_in: "peek" }, surface: "graphite", accent: "none", colour_amount: "subtle", derived: false }],
  "GET /update": [200, { installed: "1.4.0", latest: "1.4.0", channel: "stable", behind: false, checked_at: null }],
  "GET /health": [200, { status: "ok", invalid_templates: {}, invalid_policy: [], bind: "127.0.0.1", port: 8765 }],
};
const pattern = (route: string) => new RegExp(`^/ng${route.replace(/:[^/]+/g, "[^/]+")}/?$`);
const tap = async (t: Tap) => {
  if ("css" in t) return void (await userEvent.click(await waitFor(() => { const el = document.querySelector<HTMLElement>(t.css); if (!el) throw new Error(`no ${t.css}`); return el; })));
  await userEvent.click(await screen.findByRole(t.role, { name: new RegExp(t.name, "i") }));
};

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("every screen is reachable by tapping from the board (P.1)", () => {
  // A screen whose taps need a seeded item or row is walked by the sweep's flow-ng-phone-reach, against the full mock API.
  it.each(SCREENS.filter((s) => !s.data))("$id: $taps.length taps from the board land on $route", async (s) => {
    stubFetch(EMPTY);
    window.history.pushState({}, "", "/ng/");
    render(<PhoneApp />);
    await screen.findByRole("heading", { level: 1, name: "Board" });
    for (const t of s.taps) await tap(t);
    await waitFor(() => expect(window.location.pathname).toMatch(pattern(s.route)));
    expect(await screen.findByRole("heading", { level: 1, name: s.heading })).toBeInTheDocument();
  });

  it.each(SCREENS.filter((s) => !s.data && s.taps.length > 0))("$id: Back goes to its parent and stays in the app", async (s) => {
    stubFetch(EMPTY);
    window.history.pushState({}, "", "/ng/");
    render(<PhoneApp />);
    await screen.findByRole("heading", { level: 1, name: "Board" });
    for (const t of s.taps) await tap(t);
    await screen.findByRole("heading", { level: 1, name: s.heading });
    const back = document.querySelector<HTMLElement>(".ph-back");
    if (!back) return; // a root: the tab bar is its way out
    const parent = parentOf(window.location.pathname.replace(/^\/ng/, "") + window.location.search);
    await userEvent.click(back);
    await waitFor(() => expect(window.location.pathname).toBe(`/ng${parent === "/" ? "/" : parent}`));
  });
});
