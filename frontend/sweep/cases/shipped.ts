import { STATES, type DisplayState } from "../fixtures";
import { scrollAllToBottom } from "../checks";
import { idOf, ALL, KEY, SIDE, SHELLS_1280, settle, type Case, type Ctx } from "../cellKit";
import type { Page } from "@playwright/test";

async function board(c: Ctx) {
  await c.page.goto("/");
  await c.page.locator('[data-testid="board-card"], .board-row, .board-empty, .empty').first().waitFor({ timeout: 8000 }).catch(() => {});
  await settle(c.page);
}
async function peek(c: Ctx, st: DisplayState = "gate") {
  await board(c);
  const row = c.page.locator(`[data-testid="board-card"]:has-text("${c.S.byState[st].item.title.slice(0, 24)}")`).first();
  // Phone opens the peek with a long-press; a tap navigates (m03, as flow-peek-open-close does).
  if (c.page.viewportSize()!.width < 768) {
    await row.scrollIntoViewIfNeeded();
    const b = (await row.boundingBox())!;
    await c.page.mouse.move(b.x + 40, b.y + 20);
    await c.page.mouse.down();
    await c.page.waitForTimeout(650);
    await c.page.mouse.up();
  } else await row.click();
  await c.page.getByLabel("peek").waitFor({ timeout: 5000 });
  await settle(c.page);
}
async function item(c: Ctx, st: DisplayState, hash = "") {
  await c.page.goto(`/work-items/${idOf(c.S, st)}${hash}`);
  await c.page.locator(".detail, .item-page, .phone-item").first().waitFor({ timeout: 8000 });
  await settle(c.page, 600);
}
async function clickBtn(page: Page, name: RegExp) {
  const b = page.getByRole("button", { name }).first();
  // W11 · A: a state's rarer actions (Escalate, Skip) are rows under the item card's More actions.
  const more = page.locator('.item-card [aria-haspopup="menu"]').first();
  if (!(await b.isVisible().catch(() => false)) && (await more.count())) {
    await more.click();
    await page.getByRole("menuitem", { name }).first().click();
    await settle(page);
    return;
  }
  await b.waitFor({ timeout: 4000 });
  await b.click();
  await settle(page);
}
const LONG_NOTE = "The spec misses the error path entirely. When git_scan sees a blob_sha change mid-query the cache returns the stale embedding and the search ranks the old text. Add an invalidation section, and say what happens for submodules — they have their own HEAD.\n\nAlso: the LRU cap is per process; with two daemons it is effectively doubled.";

async function composer(c: Ctx, st: DisplayState, open: RegExp, fill: boolean) {
  await item(c, st);
  await clickBtn(c.page, open);
  if (fill) {
    const ta = c.page.getByLabel("composer message").or(c.page.locator("textarea")).first();
    await ta.fill(LONG_NOTE).catch(() => {});
    await settle(c.page);
  }
}
async function settings(c: Ctx, to: string) {
  await c.page.goto(`/settings/${to}`);
  await c.page.locator("main").waitFor();
  await settle(c.page, 600);
}

export const cells: Case[] = [
  // Board
  { screen: "board", variant: "default", data: "default", widths: ALL, shells: SHELLS_1280, firstpaint: true, run: board },
  { screen: "board", variant: "long", data: "long", widths: ALL, run: board },
  { screen: "board", variant: "many", data: "many", widths: KEY, run: board },
  { screen: "board", variant: "many-scrolled", data: "many", widths: KEY, run: async (c) => { await board(c); await scrollAllToBottom(c.page); } },
  { screen: "board", variant: "empty", data: "empty", widths: KEY, run: board },
  { screen: "board", variant: "group-repo", data: "default", widths: [1280], shells: [{ group_by: "repo" }], run: board },
  { screen: "board", variant: "group-template", data: "long", widths: [1280], shells: [{ group_by: "template" }], run: board },
  // W14 · C.2: the two cells handoff_v4 names (d07, m03). Two done rows ticked → the selection bar.
  { screen: "board", variant: "selection-bar", data: "default", widths: [1280], run: async (c) => { await board(c); const boxes = c.page.locator('.board-row input[type="checkbox"]'); await boxes.nth(0).check(); await boxes.nth(1).check(); await c.page.locator(".board-floating-bar").waitFor({ timeout: 4000 }); await settle(c.page); } },
  // As built the repo sheet opens from the phone's repo pill; a long-press on a row opens the peek (m03's
  // "long-press → sheet" was that peek sheet), so this cell taps the pill.
  { screen: "board", variant: "repo-sheet", data: "default", widths: [390], run: async (c) => { await board(c); await c.page.locator(".repo-pill").click(); await c.page.locator(".repo-sheet").waitFor({ timeout: 4000 }); await settle(c.page); } },
  { screen: "board-peek", variant: "gate", data: "default", widths: [...ALL, ...SIDE], shells: SHELLS_1280, run: (c) => peek(c, "gate") },
  { screen: "board-peek", variant: "running-long", data: "long", widths: [...ALL, ...SIDE], run: (c) => peek(c, "running") },
  { screen: "board-peek", variant: "escalated-long", data: "long", widths: [...KEY, ...SIDE], run: (c) => peek(c, "escalated") },
  { screen: "board-peek", variant: "capped", data: "default", widths: [...KEY, ...SIDE], run: (c) => peek(c, "capped") },
  { screen: "board-peek", variant: "done", data: "default", widths: [390, 1280], run: (c) => peek(c, "done") },
  // W11 · J: the escalating item, now under Running.
  { screen: "board-peek", variant: "escalating", data: "default", widths: [390, 1280], run: (c) => peek(c, "escalating") },
  { screen: "archived", variant: "default", data: "default", widths: KEY, run: async (c) => { await c.page.goto("/archived"); await settle(c.page, 600); } },
  { screen: "archived", variant: "empty", data: "empty", widths: [1280], run: async (c) => { await c.page.goto("/archived"); await settle(c.page, 600); } },

  // Item page · every state, default tab
  ...STATES.map<Case>((st) => ({ screen: "item", variant: st, data: "default", widths: ["gate", "running", "capped"].includes(st) ? [...ALL, ...SIDE] : KEY, run: (c) => item(c, st) })),
  ...STATES.map<Case>((st) => ({ screen: "item", variant: `${st}-long`, data: "long", widths: ["gate", "running"].includes(st) ? [...ALL, ...SIDE] : [390, 1100, 1920], run: (c) => item(c, st) })),
  { screen: "item", variant: "gate-sidebar-open", data: "long", widths: [1100, 1280, 1440], shells: [{ sidebar: "open" }], run: (c) => item(c, "gate") },
  { screen: "item", variant: "gate-h700", data: "long", widths: [1280, 1920], shells: [{ short: true }], run: (c) => item(c, "gate") },
  { screen: "item", variant: "gate-light", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => item(c, "gate") },
  { screen: "item", variant: "gate-comfortable", data: "default", widths: [1280], shells: [{ density: "comfortable" }], run: (c) => item(c, "gate") },

  // Item tabs (gate item has the richest data)
  ...(["tasks", "changes", "documents", "timeline", "config"] as const).flatMap<Case>((tab) => [
    { screen: `item-${tab}`, variant: "default", data: "default", widths: ALL, run: async (c) => { await item(c, "gate", `#tab=${tab}`); await firstRow(c, tab); } },
    { screen: `item-${tab}`, variant: "long", data: "long", widths: KEY, run: async (c) => { await item(c, "gate", `#tab=${tab}`); await firstRow(c, tab); } },
    { screen: `item-${tab}`, variant: "long-scrolled", data: "long", widths: [390, 1280], run: async (c) => { await item(c, "gate", `#tab=${tab}`); await firstRow(c, tab); await scrollAllToBottom(c.page); } },
  ]),
  { screen: "item-changes", variant: "landed-file", data: "long", widths: KEY, run: async (c) => { await item(c, "gate", "#tab=changes"); await c.page.locator('.tree-row[data-kind="file"]').last().click().catch(() => {}); await settle(c.page); } },
  { screen: "item-changes", variant: "folder-collapsed", data: "long", widths: [1280], run: async (c) => { await item(c, "gate", "#tab=changes"); for (const f of await c.page.locator('.tree-row[data-kind="dir"]').all()) await f.click().catch(() => {}); await settle(c.page); } },
  { screen: "item-documents", variant: "running-long", data: "long", widths: [390, 1280], run: async (c) => { await item(c, "running", "#tab=documents"); await firstRow(c, "documents"); } },
  { screen: "item-timeline", variant: "capped", data: "default", widths: [390, 1280], run: async (c) => { await item(c, "capped", "#tab=timeline"); await firstRow(c, "timeline"); } },
  { screen: "item-config", variant: "not-started", data: "long", widths: KEY, run: (c) => item(c, "not_started", "#tab=config") },
  { screen: "item-log", variant: "maximized", data: "default", widths: ALL, run: async (c) => { await item(c, "running", "#tab=tasks"); await firstRow(c, "tasks"); await clickBtn(c.page, /maximize/i).catch(() => {}); } },
  { screen: "item-log", variant: "maximized-long", data: "long", widths: KEY, run: async (c) => { await item(c, "running", "#tab=tasks"); await firstRow(c, "tasks"); await clickBtn(c.page, /maximize/i).catch(() => {}); } },
  { screen: "item-log", variant: "maximized-long-scrolled-up", data: "long", widths: [1280], run: async (c) => { await item(c, "running", "#tab=tasks"); await firstRow(c, "tasks"); await clickBtn(c.page, /maximize/i).catch(() => {}); await c.page.mouse.move(640, 400); await c.page.mouse.wheel(0, -3000); await settle(c.page); } },
  { screen: "item-log", variant: "done-session", data: "long", widths: [390, 1280], run: async (c) => { await item(c, "done", "#tab=tasks"); await firstRow(c, "tasks"); } },

  // Composers · empty and filled
  ...([
    ["steer", "paused", /^Steer$/],
    ["steer-retry", "capped", /Steer & retry/],
    ["reject", "gate", /^Reject$/],
    ["answer", "question", /^Answer$/],
    ["escalate", "gate", /^Escalate/],
    ["escalate-thread", "escalated", /^Reply/],
    ["raise-budget", "budget", /Raise budget/],
  ] as [string, DisplayState, RegExp][]).flatMap<Case>(([name, st, btn]) => [
    { screen: "composer", variant: `${name}`, data: "default", widths: KEY, run: (c) => composer(c, st, btn, false) },
    { screen: "composer", variant: `${name}-filled-long`, data: "long", widths: KEY, run: (c) => composer(c, st, btn, true) },
  ]),
  // Kraft-dkb6g: a prior thread's composer (folded rows + divider + split
  // button) and the split button's caret menu open.
  { screen: "composer", variant: "escalate-prior-thread", data: "long", widths: [390, 1280], run: async (c) => { await item(c, "escalated"); await clickBtn(c.page, /reply/i).catch(() => {}); await settle(c.page); } },
  { screen: "composer", variant: "reply-caret", data: "long", widths: [1280], run: async (c) => { await item(c, "escalated"); await clickBtn(c.page, /reply/i).catch(() => {}); await c.page.getByRole("button", { name: /reply options/i }).click().catch(() => {}); await settle(c.page); } },
  { screen: "composer", variant: "escalating-pill", data: "default", widths: KEY, run: (c) => item(c, "escalating") },
  { screen: "composer", variant: "gate-skip-menu", data: "default", widths: [390, 1280], run: async (c) => { await item(c, "gate"); await clickBtn(c.page, /skip/i).catch(() => {}); } },
  { screen: "composer", variant: "overflow-menu", data: "default", widths: [390, 1280], run: async (c) => { await item(c, "running"); await c.page.locator('.item-card [aria-haspopup="menu"]').first().click().catch(() => {}); await settle(c.page); } },
  // The app header's `…` on an item page, found by aria-haspopup rather than a guessed label.
  // Desktop only: a phone item page has PhoneTopBar, and its item menu is composer/overflow-menu@390 (Kraft-92daa).
  { screen: "menu", variant: "header-more", data: "default", widths: [1280], run: async (c) => { await item(c, "running"); await c.page.locator('.app-header [aria-haspopup="menu"]').first().click(); await settle(c.page); } },

  // Intake modal
  { screen: "new-item", variant: "empty", data: "default", widths: ALL, run: async (c) => { await board(c); await clickBtn(c.page, /new work item/i); } },
  { screen: "new-item", variant: "filled-long", data: "long", widths: KEY, run: async (c) => {
      await board(c); await clickBtn(c.page, /new work item/i);
      const modal = c.page.getByRole("dialog", { name: "New work item" });
      await modal.getByLabel("title").fill(c.S.byState.gate.item.title).catch(() => {});
      await modal.getByLabel("description").fill(c.S.byState.gate.item.description ?? LONG_NOTE).catch(() => {});
      await modal.getByRole("radiogroup", { name: "template" }).getByRole("radio").first().click().catch(() => {});
      await settle(c.page);
    } },
  { screen: "new-item", variant: "scrolled", data: "long", widths: [390, 1280], run: async (c) => { await board(c); await clickBtn(c.page, /new work item/i); await scrollAllToBottom(c.page); } },

  // Search
  { screen: "search", variant: "overlay-results", data: "default", widths: KEY, run: async (c) => { await board(c); await c.page.keyboard.press("Meta+k"); await c.page.locator('input[aria-label="search"]').fill("measured"); await settle(c.page, 800); } },
  { screen: "search", variant: "overlay-long", data: "long", widths: KEY, run: async (c) => { await board(c); await c.page.keyboard.press("Meta+k"); await c.page.locator('input[aria-label="search"]').fill("reuse what we measured"); await settle(c.page, 800); } },
  { screen: "search", variant: "overlay-empty", data: "empty", widths: [390, 1280], run: async (c) => { await board(c); await c.page.keyboard.press("Meta+k"); await c.page.locator('input[aria-label="search"]').fill("zzz"); await settle(c.page, 800); } },
  // /search does not read ?q= -- these cases shot an empty page in every round. Type the query, as the overlay cases do (W11 · H).
  { screen: "search", variant: "page", data: "long", widths: KEY, run: async (c) => { await c.page.goto("/search"); await c.page.locator('input[aria-label="search"]').fill("measured"); await settle(c.page, 800); } },
  { screen: "search", variant: "doc-viewer", data: "long", widths: KEY, run: async (c) => { await c.page.goto("/search"); await c.page.locator('input[aria-label="search"]').fill("measured"); await settle(c.page, 800); await c.page.locator(".search-result").filter({ hasText: /reuse|plan|review/i }).first().click().catch(() => {}); await settle(c.page, 600); } },

  // Analytics
  { screen: "analytics", variant: "default", data: "default", widths: ALL, run: async (c) => { await c.page.goto("/analytics"); await settle(c.page, 800); } },
  { screen: "analytics", variant: "long", data: "long", widths: KEY, run: async (c) => { await c.page.goto("/analytics"); await settle(c.page, 800); } },
  { screen: "analytics", variant: "empty", data: "empty", widths: [390, 1280], run: async (c) => { await c.page.goto("/analytics"); await settle(c.page, 800); } },

  // Settings × 9 (+ sub-states)
  ...["repos", "chains", "plugins", "policy", "steering", "intake", "notify", "access", "appearance"].flatMap<Case>((to) => [
    { screen: `settings-${to}`, variant: "default", data: "default", widths: ALL, run: (c) => settings(c, to) },
    { screen: `settings-${to}`, variant: "long", data: "long", widths: KEY, run: (c) => settings(c, to) },
    { screen: `settings-${to}`, variant: "empty", data: "empty", widths: [1280], run: (c) => settings(c, to) },
    { screen: `settings-${to}`, variant: "sidebar-open", data: "long", widths: [1100, 1280], shells: [{ sidebar: "open" }], run: (c) => settings(c, to) },
  ]),
  { screen: "settings-repos", variant: "repo-page", data: "long", widths: KEY, run: async (c) => { await settings(c, "repos"); await c.page.locator("main").getByRole("link").or(c.page.locator("main .row, main li, main tr")).filter({ hasText: /kraft/ }).first().click().catch(() => {}); await settle(c.page, 600); } },
  { screen: "settings-repos", variant: "add-repo", data: "default", widths: [390, 1280], run: async (c) => { await settings(c, "repos"); await clickBtn(c.page, /add|connect/i).catch(() => {}); } },
  // W11 · D removed the templates column these clicked "default" in: open the editor card on a node instead.
  { screen: "settings-chains", variant: "editor", data: "long", widths: KEY, run: async (c) => { await settings(c, "chains"); await c.page.locator(".chain-pill", { hasText: /^verify/ }).first().click().catch(() => {}); await settle(c.page, 600); } },
  { screen: "settings-chains", variant: "editor-scrolled", data: "long", widths: [390, 1280], run: async (c) => { await settings(c, "chains"); await c.page.locator(".chain-pill", { hasText: /^verify/ }).first().click().catch(() => {}); await scrollAllToBottom(c.page); } },
  { screen: "settings-plugins", variant: "hook-runs", data: "long", widths: [390, 1280], run: async (c) => { await settings(c, "plugins"); await c.page.locator("main").getByText(/on\.implementation\.start/).first().click().catch(() => {}); await settle(c.page, 600); } },
  { screen: "settings-steering", variant: "file-open", data: "long", widths: KEY, run: async (c) => { await settings(c, "steering"); await c.page.locator("main").getByText(/house-style/).first().click().catch(() => {}); await settle(c.page, 600); } },
  { screen: "settings-index", variant: "default", data: "default", widths: [390, 1280], run: async (c) => { await c.page.goto("/settings"); await settle(c.page, 600); } },

  // Login
  { screen: "login", variant: "default", data: "default", widths: KEY, locked: true, run: async (c) => { await c.page.goto("/"); await settle(c.page, 800); } },
  { screen: "login", variant: "filled", data: "default", widths: [390, 1280], locked: true, run: async (c) => { await c.page.goto("/"); await settle(c.page, 600); await c.page.locator('input[type="password"]').fill("hunter2").catch(() => {}); await settle(c.page); } },
];

async function firstRow(c: Ctx, tab: string) {
  const sel: Record<string, string> = {
    tasks: '[data-testid^="task-row-"]',
    changes: '.tree-row[data-kind="file"]',
    documents: '[data-testid="inspector-documents"] li, [data-testid="inspector-documents"] [role="option"], [data-testid="inspector-documents"] button',
    timeline: '[data-testid="inspector-timeline"] li, [data-testid="inspector-timeline"] button, [data-testid="inspector-timeline"] .row',
    config: "",
  };
  if (!sel[tab]) return;
  await c.page.locator(sel[tab]).first().click({ timeout: 3000 }).catch(() => {});
  await settle(c.page, 500);
}
