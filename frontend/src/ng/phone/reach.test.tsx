import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubFetch } from "../item/testkit";
import { parentOf } from "./nav/route";
import { PhoneApp } from "./PhoneApp";
import { SCREENS, type PhoneScreen, type Tap } from "./screens";

/** What the screens read before any item exists: each route's own empty shape. */
const EMPTY: Record<string, [number, unknown]> = {
  "GET /work-items": [200, { items: [], cursor: 0 }],
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
const pattern = (route: string) => new RegExp(`^${route.replace(/:[^/]+/g, "[^/]+")}/?$`);
const tap = async (t: Tap) => {
  if ("css" in t) return void (await userEvent.click(await waitFor(() => { const el = document.querySelector<HTMLElement>(t.css); if (!el) throw new Error(`no ${t.css}`); return el; })));
  await userEvent.click(await screen.findByRole(t.role, { name: new RegExp(t.name, "i") }));
};

/** Opens the app on a board that loaded (empty, and not offline), then taps to the screen. */
async function walkTo(s: PhoneScreen) {
  stubFetch(EMPTY);
  window.history.pushState({}, "", "/");
  render(<PhoneApp />);
  await screen.findByRole("heading", { level: 1, name: "Board" });
  await screen.findByText("Nothing here. Tap + to file one.");
  expect(screen.queryByText("offline")).toBeNull();
  for (const t of s.taps) await tap(t);
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("every screen is reachable by tapping from the board (P.1)", () => {
  // A screen whose taps need a seeded item or row is walked by the UI contract (e2e/contract/phone.spec.ts), against the full mock API.
  it.each(SCREENS.filter((s) => !s.data))("$id: $taps.length taps from the board land on $route", async (s) => {
    await walkTo(s);
    await waitFor(() => expect(window.location.pathname).toMatch(pattern(s.route)));
    expect(await screen.findByRole("heading", { level: 1, name: s.heading })).toBeInTheDocument();
  });

  const walked = SCREENS.filter((s) => !s.data && s.taps.length > 0);
  it.each(walked.filter((s) => parentOf(s.route) !== null))("$id: Back goes to its parent and stays in the app", async (s) => {
    await walkTo(s);
    await screen.findByRole("heading", { level: 1, name: s.heading });
    const back = document.querySelector<HTMLElement>(".ph-back");
    expect(back, "a screen with a parent shows Back").not.toBeNull();
    const parent = parentOf(window.location.pathname + window.location.search);
    await userEvent.click(back!);
    await waitFor(() => expect(window.location.pathname).toBe(parent));
  });

  it.each(walked.filter((s) => parentOf(s.route) === null))("$id: a root shows no Back, the tab bar being its way out", async (s) => {
    await walkTo(s);
    await screen.findByRole("heading", { level: 1, name: s.heading });
    expect(document.querySelector(".ph-back")).toBeNull();
  });
});
