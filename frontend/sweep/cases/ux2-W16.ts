import { NG_NOW } from "../ngItems";
import { ng, settle, type Case } from "../cellKit";

export const cells: Case[] = [
  // W16 D: the sections below the colour ones, each scrolled to its heading.
  { screen: "appearance", variant: "syntax-monokai", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: async (c) => {
    await ng(c, "/settings/appearance", { surface: "slate", accent: "blue", colour_amount: "subtle", code_scheme: { light: "solarized-light", dark: "monokai" } });
    await c.page.getByRole("heading", { name: "Syntax highlighting" }).evaluate((h) => h.scrollIntoView({ block: "start" })); await settle(c.page, 300);
  } },
  { screen: "appearance", variant: "diff-prefs", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: async (c) => {
    await ng(c, "/settings/appearance", { diff: { layout: "split", colours: "safe", show_whitespace: true, word_highlight: true, wrap_lines: true, one_file_at_a_time: false } });
    await c.page.getByRole("heading", { name: "Review diff" }).evaluate((h) => h.scrollIntoView({ block: "start" })); await settle(c.page, 300);
  } },
  { screen: "appearance", variant: "density", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: async (c) => {
    await ng(c, "/settings/appearance", { density: "comfortable" });
    await c.page.getByRole("heading", { name: "Board" }).evaluate((h) => h.scrollIntoView({ block: "start" })); await settle(c.page, 300);
  } },

  // W16 B: Access. Health is the running server: the page compares it with the saved bind and port.
  ...([
    ["default", "default", {}, {}, "none"],
    ["restart-pending", "default", { port: 9100 }, {}, "restart"],
    ["env-locked", "default", { port: 9100 }, {}, "none"],
    ["sessions-empty", "default", {}, { sessions: [] }, "none"],
    ["long", "long", {}, {}, "none"],
    ["loopback", "empty", {}, {}, "none"],
  ] as const).map<Case>(([variant, data, access, extra, apply]) => ({
    screen: "access", variant, data, widths: [1280, 1920], shells: [{ mode: "light" }], mock: { apply },
    run: async (c) => {
      const h = c.S.settings.health;
      Object.assign(c.S.settings.access, access);
      if (c.S.settings.access.bind !== "127.0.0.1") h.bind = c.S.settings.access.bind;
      if ("sessions" in extra) c.S.settings.sessions.sessions = [...extra.sessions];
      await ng(c, "/settings/access", {});
    },
  })),

  // W16 C: Notifications. The browser's permission is stubbed: headless Chromium answers "denied" to everyone.
  ...([
    ["default", "default", {}],
    ["denied", "denied", {}],
    ["test-failed", "granted", { last_test: { at: new Date(Date.UTC(2026, 8, 13, 9, 50)).toISOString(), status: null, ms: null, error: "connection refused" } }],
    ["not-set-up", "default", { enabled: false, url_set: false, events: [], base_url: null, last_test: null }],
  ] as const).map<Case>(([variant, permission, notify]) => ({
    screen: "notifications", variant, data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], mock: { apply: "none" },
    run: async (c) => {
      await c.page.clock.setFixedTime(new Date(NG_NOW));
      await c.page.addInitScript((p) => { (window as any).Notification = class { static permission = p; static requestPermission = async () => p; }; }, permission);
      Object.assign(c.S.settings.notify, notify);
      await ng(c, "/settings/notifications", {});
    },
  })),

  // W16 E: About. The feed's answer decides the verdict; an unreachable feed reads "unknown".
  ...(["available", "current", "unknown"] as const).map<Case>((update) => ({
    screen: "about", variant: update === "available" ? "default" : update, data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], mock: { update },
    run: (c) => ng(c, "/settings/about", {}),
  })),

  // W16 F: Analytics, fed by the same analyticsFor() fixtures as the shipped page.
  { screen: "analytics", variant: "default", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: async (c) => { await c.page.clock.setFixedTime(new Date(NG_NOW)); await ng(c, "/analytics", {}); } },
  { screen: "analytics", variant: "long", data: "long", widths: [1280, 1920], shells: [{ mode: "light" }], run: async (c) => { await c.page.clock.setFixedTime(new Date(NG_NOW)); await ng(c, "/analytics", {}); } },
  { screen: "analytics", variant: "empty", data: "empty", widths: [1280, 1920], shells: [{ mode: "light" }], run: async (c) => { await c.page.clock.setFixedTime(new Date(NG_NOW)); await ng(c, "/analytics", {}); } },

  // W16 A: the apply chip, its popover open, on a page of its own.
  ...([["reload", "Changed on disk, 1"], ["restart", "Restart needed, 1"], ["problem", "Reload found a problem, 1"], ["unmanaged", "Restart needed, 1"]] as const).map<Case>(([v, name]) => ({
    screen: "apply", variant: v, data: "default", widths: [1280], shells: v === "restart" ? [{ mode: "light" }] : undefined, mock: { apply: v },
    run: async (c) => { await ng(c, "/settings/appearance", {}); await c.page.getByRole("button", { name }).click(); await c.page.getByRole("dialog", { name: "Waiting to apply" }).waitFor(); await settle(c.page, 300); },
  })),
  { screen: "apply", variant: "confirm", data: "default", widths: [1280], mock: { apply: "restart" }, run: async (c) => {
    await ng(c, "/settings/appearance", {});
    await c.page.getByRole("button", { name: "Restart needed, 1" }).click(); await c.page.getByRole("button", { name: "Restart Kraft" }).click();
    await c.page.getByRole("dialog", { name: "Restart Kraft?" }).waitFor(); await settle(c.page, 300);
  } },
];
