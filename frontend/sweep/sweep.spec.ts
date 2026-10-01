import { test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { buildScenario, settingsFor, STATES, type DisplayState, type Scenario, type Variant } from "./fixtures";
import { installMocks, type MockOptions } from "./mockApi";
import { NG_NOW, NG_SCENARIOS } from "./ngItems";
import { chromeRects, runChecks, scrollAllToBottom, type Checks } from "./checks";

/**
 * The UI sweep: every screen × data variant × viewport × shell state, each
 * screenshotted and machine-checked, appended to e2e-shots/sweep/manifest.jsonl.
 * Nothing here asserts — a failed setup is recorded on the manifest entry and
 * the shot is still taken, so one broken selector never hides 300 screens.
 *
 * Filter with env: SWEEP_SCREEN=board,item  SWEEP_WIDTHS=390,1280  SWEEP_VARIANT=long
 */

const OUT = path.resolve("e2e-shots/sweep");
fs.mkdirSync(OUT, { recursive: true });
const MANIFEST = path.join(OUT, "manifest.jsonl");

const VP: Record<number, [number, number]> = {
  390: [390, 844], 768: [768, 1024], 960: [960, 800], 1000: [1000, 800], 1024: [1024, 768], 1100: [1100, 800],
  1280: [1280, 800], 1440: [1440, 900], 1920: [1920, 1080],
};
const ALL = [390, 768, 1024, 1100, 1280, 1440, 1920];
const KEY = [390, 1100, 1280, 1920];
// A desktop browser with a side panel open (W10.D): the peek and the item page just under 1024.
const SIDE = [960, 1000];

interface Shell { sidebar?: "open" | "rail"; mode?: "light" | "dark"; density?: "compact" | "comfortable"; group_by?: "repo" | "template"; short?: boolean; firstpaint?: boolean }
const SHELLS_1280: Shell[] = [{ sidebar: "open" }, { sidebar: "rail" }, { mode: "light" }, { density: "comfortable" }, { short: true }];
const shellId = (s: Shell) => [s.sidebar, s.mode, s.density, s.group_by, s.short ? "h700" : "", s.firstpaint ? "firstpaint" : ""].filter(Boolean).join("-") || "auto";

interface Ctx { page: Page; S: Scenario; width: number; shell: Shell }
interface Case {
  screen: string;
  variant: string;
  data: Variant;
  widths: number[];
  shells?: Shell[];
  locked?: boolean;
  /** With `locked`: what the mock's POST /login answers. */
  login?: "ok" | "wrong" | "locked";
  fullPage?: boolean;
  /** Also shoot `~light-firstpaint` at 1280: reload and screenshot at DOMContentLoaded, 0ms settle. */
  firstpaint?: boolean;
  /** More of the mock's options (ux2-W6: the /ng board's fixtures and states). */
  mock?: MockOptions;
  run: (c: Ctx) => Promise<void>;
}

/* ── helpers ─────────────────────────────────────────────────────────────── */

const settle = (page: Page, ms = 400) => page.waitForTimeout(ms);
const idOf = (S: Scenario, st: DisplayState) => S.byState[st].item.id;

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
/** The /ng Chains editor on one of the mock's drafts, sidebar pinned, once the canvas has drawn. */
async function ngChains(c: Ctx, key: string, node?: string) {
  await c.page.addInitScript(() => localStorage.setItem("kraft.sidebar.v2", "pinned"));
  await c.page.goto(`/ng/templates/chains/${key}${node ? `/nodes/${node}` : ""}`);
  await c.page.locator(".canvas, .tpl-note").first().waitFor({ timeout: 8000 });
  await settle(c.page, 700);
}
/** A /ng page under a given look: the mock's theme is what GET /theme answers. */
async function ng(c: Ctx, url: string, look: Record<string, unknown>, opts: { side?: "pinned" | "rail"; hover?: boolean } = {}) {
  Object.assign(c.S.settings.theme, look);
  if (opts.side) await c.page.addInitScript((v) => localStorage.setItem("kraft.sidebar.v2", v), opts.side);
  await c.page.goto(url);
  await c.page.locator("main h1").first().waitFor({ timeout: 8000 });
  await settle(c.page, 600);
  if (opts.hover) { await c.page.mouse.move(20, 300); await settle(c.page, 500); }
}
/** The /ng item page (ux2-W5) for one of ngItems.ts's scenarios, the clock fixed at NG_NOW so elapsed reads the same every run. */
async function ngItem(c: Ctx, sc: string, opts: { tail?: string; side?: "pinned" | "rail"; then?: (p: Page) => Promise<void> } = {}) {
  await c.page.clock.setFixedTime(new Date(NG_NOW));
  await ng(c, `/ng/work-items/${c.S.ng[sc]}${opts.tail ?? ""}`, {}, { side: opts.side ?? "pinned" });
  if (opts.then) { await opts.then(c.page); await settle(c.page, 400); }
}
/** The /ng board (ux2-W6), served its own fixtures (`mock: { ngBoard }`), the clock fixed at NG_NOW so ages read the same every run. */
async function ngBoard(c: Ctx, opts: { tail?: string; side?: "pinned" | "rail"; then?: (p: Page) => Promise<void> } = {}) {
  await c.page.clock.setFixedTime(new Date(NG_NOW));
  await ng(c, `/ng/${opts.tail ?? ""}`, {}, { side: opts.side ?? "pinned" });
  if (opts.then) { await opts.then(c.page); await settle(c.page, 400); }
}
/** The composer with its repos, chains and first dry run in. */
const composerReady = async (p: Page) => { await p.getByText(/nodes run ·/).waitFor(); await p.waitForTimeout(400); };
const composerFilled = async (p: Page) => {
  await composerReady(p);
  await p.getByRole("textbox", { name: "Title" }).fill("Design the caching layer for document search");
  await p.getByRole("textbox", { name: "Brief" }).fill("Cache embeddings by content hash; invalidate on reindex.");
  await p.getByRole("button", { name: "+ spec" }).click();
  const box = p.getByRole("textbox", { name: /Search specs/ });
  await box.fill("docs/specs/doc-search-cache.md"); await p.waitForTimeout(300); await box.press("Enter");
  await p.getByText(/of 15 nodes run/).waitFor(); await p.waitForTimeout(500);
};
/** The draft item page from a typed URL (its query carries the draft), titled with a spec or empty. */
const ngDraft = async (c: Ctx, titled: boolean, then?: (p: Page) => Promise<void>) => {
  const q = new URLSearchParams({ repo: "/Users/dev/code/kraft-plugins", chain: "default", ...(titled ? { title: "Design the caching layer for document search", spec: "docs/specs/doc-search-cache.md" } : {}) });
  await ngBoard(c, { tail: `work-items/new?${q}`, then: async (p) => { await p.getByText(/nodes run ·/).first().waitFor(); await p.waitForTimeout(500); if (then) await then(p); } });
};
/** A board row per peek variant, by the start of its title (ngBoard.ts). */
const PEEK_ROWS = {
  "needs-gate": "Design the caching layer", question: "Add rate limit headers", capped: "Fix flaky retry test", failed: "Retry on 429",
  paused: "Trim the review prompts", running: "Bump the VS Code", "not-started": "Spike: stream logs", done: "Release notes for 0.14", cancelled: "Rename the harness profiles",
} as const;
/** Click a board row and wait for its peek. */
const peekRow = (title: string) => async (p: Page) => {
  await p.getByRole("button", { name: new RegExp(`^${title}`) }).click();
  await p.locator(".pane .pane-tabs").waitFor();
};
/** Check board rows by the start of their titles. */
const checkRows = (titles: string[]) => async (p: Page) => {
  for (const t of titles) await p.getByRole("checkbox", { name: new RegExp(`^Select ${t}`) }).check();
};
/** The /ng review page on the needs-gate item. `settings`: Diff settings rows to click first (saved to the mock's theme). */
async function ngReview(c: Ctx, opts: { sc?: string; tail?: string; side?: "pinned" | "rail"; settings?: string[]; then?: (p: Page) => Promise<void> } = {}) {
  await ngItem(c, opts.sc ?? "needs-gate", { tail: `/review${opts.tail ?? ""}`, side: opts.side });
  const p = c.page;
  if (opts.settings?.length) {
    await p.getByRole("button", { name: "Diff settings" }).click();
    for (const name of opts.settings) await p.getByRole("menuitemradio", { name }).or(p.getByRole("menuitemcheckbox", { name })).click();
    await p.keyboard.press("Escape");
    await settle(p, 300);
  }
  if (opts.then) { await opts.then(p); await settle(p, 400); }
}
/** Pick new line 5 of the review's file by its number, open the composer with Enter, type. */
async function ngComment(p: Page, text: string) {
  await p.getByRole("button", { name: "Pick new line 5", exact: true }).first().click();
  await p.getByRole("group", { name: /^Lines of / }).first().press("Enter");
  await p.getByRole("textbox", { name: "Comment" }).fill(text);
}
/** The /ng search overlay: open it with Ctrl+K, optionally type, and wait for the debounced sections. */
async function ngSearch(c: Ctx, q: string, opts: { docsError?: boolean; noBeads?: boolean } = {}) {
  if (opts.noBeads) await c.page.route("**/api/beads/search*", (r) => r.fulfill({ status: 200, contentType: "application/json", body: '{"query":"","beads":[]}' }));
  if (opts.docsError) await c.page.route("**/api/search*", (r) => r.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"index unavailable"}' }));
  await ng(c, "/ng/settings/access", {}, { side: "pinned" });
  await c.page.keyboard.press("Control+k");
  const box = c.page.getByRole("combobox", { name: /search/i });
  await box.waitFor({ timeout: 4000 });
  if (q) { await box.fill(q); await c.page.waitForTimeout(700); }
  await settle(c.page, 400);
}
/** The /ng sign-in card; `submit` types a password and presses Enter, against the mock's 401 or 429. The clock's fixed so the countdown reads the same every run. */
async function ngLogin(c: Ctx, opts: { fill?: boolean; submit?: boolean } = {}) {
  await c.page.clock.setFixedTime(new Date("2026-01-01T00:00:00Z"));
  await ng(c, "/ng", {});
  if (opts.fill || opts.submit) await c.page.getByLabel(/^Password/).fill("hunter2");
  if (opts.submit) { await c.page.keyboard.press("Enter"); await c.page.locator('[role="alert"], [role="timer"]').first().waitFor({ timeout: 4000 }); }
  await settle(c.page, 400);
}
/** The /ng board with no repo connected. A fresh install has the default chain, which the "empty" data lacks, so it's put back. The page clock is installed after load, so the probe rows' reveal steps are ours to advance. */
async function ngFirstRun(c: Ctx, stage: "step1" | "probing" | "probed" | "step2" | "step3") {
  c.S.settings.templates = settingsFor("default", {}).templates;
  await ng(c, "/ng", {}, { side: "pinned" });
  await c.page.getByRole("heading", { name: "Nothing on the board yet" }).waitFor({ timeout: 4000 });
  if (stage === "step1") return;
  await c.page.clock.install();
  await c.page.getByLabel(/Path to a local git checkout/).fill("/Users/dev/code/acme");
  await c.page.getByRole("button", { name: "+ Add repo" }).click();
  await c.page.getByRole("list", { name: "Probe results" }).waitFor({ timeout: 4000 });
  if (stage === "probing") { await c.page.clock.runFor(500); return settle(c.page, 200); }
  await c.page.clock.runFor(2000);
  if (stage === "probed") return settle(c.page, 200);
  await c.page.getByRole("button", { name: "Add repo", exact: true }).click();
  await c.page.getByRole("button", { name: "Continue" }).click();
  if (stage === "step3") await c.page.getByRole("button", { name: "Continue" }).click();
  await settle(c.page, 400);
}
async function settings(c: Ctx, to: string) {
  await c.page.goto(`/settings/${to}`);
  await c.page.locator("main").waitFor();
  await settle(c.page, 600);
}

/* ── the matrix ──────────────────────────────────────────────────────────── */

const CASES: Case[] = [
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

  // UX V2 under /ng. At 390 the phone redirect lands on the shipped board; the entry's `url` records where.
  { screen: "ng-shell", variant: "board-stub", data: "default", widths: [1280, 390], run: async (c) => { await c.page.goto("/ng"); await c.page.locator('h1, [data-testid="board-card"], .board-row').first().waitFor({ timeout: 8000 }); await settle(c.page); } },
  // W2 A: an unbuilt page inside the shell.
  { screen: "ng-shell", variant: "placeholder", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => ng(c, "/ng/settings/access", {}) },
  // W2 B: the sidebar. Pinned and rail by stored choice; "revealed" is the pointer over the rail.
  { screen: "ng-shell", variant: "pinned", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: (c) => ng(c, "/ng/settings/access", {}, { side: "pinned" }) },
  { screen: "ng-shell", variant: "rail", data: "default", widths: [1024], shells: [{ mode: "light" }], run: (c) => ng(c, "/ng/settings/access", {}, { side: "rail" }) },
  { screen: "ng-shell", variant: "revealed", data: "default", widths: [1024], shells: [{ mode: "light" }], run: (c) => ng(c, "/ng/settings/access", {}, { side: "rail", hover: true }) },
  { screen: "ng-shell", variant: "pinned", data: "default", widths: [1024], run: (c) => ng(c, "/ng/settings/access", {}, { side: "pinned" }) },
  { screen: "ng-shell", variant: "long-crumb", data: "long", widths: [1024], run: (c) => ng(c, `/ng/work-items/${idOf(c.S, "gate")}`, {}, { side: "rail" }) },
  { screen: "ng-shell", variant: "actions", data: "default", widths: [1280], run: (c) => ng(c, "/ng/_tokens", {}, { side: "pinned" }) },
  // W1: the token sheet per surface (both modes via the ~light shell), and Appearance's colour section.
  { screen: "ng-search", variant: "empty", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => ngSearch(c, "") },
  { screen: "ng-search", variant: "results", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => ngSearch(c, "gate") },
  { screen: "ng-search", variant: "results", data: "default", widths: [1024, 1920], run: (c) => ngSearch(c, "gate") },
  { screen: "ng-search", variant: "no-match", data: "empty", widths: [1280], run: (c) => ngSearch(c, "zzzqx", { noBeads: true }) },
  { screen: "ng-search", variant: "docs-error", data: "default", widths: [1280], run: (c) => ngSearch(c, "gate", { docsError: true }) },
  { screen: "ng-login", variant: "idle", data: "default", widths: [1280], locked: true, shells: [{ mode: "light" }], run: (c) => ngLogin(c) },
  { screen: "ng-login", variant: "idle", data: "default", widths: [1024, 1920], locked: true, run: (c) => ngLogin(c) },
  { screen: "ng-login", variant: "filled", data: "default", widths: [1280], locked: true, run: (c) => ngLogin(c, { fill: true }) },
  { screen: "ng-login", variant: "error", data: "default", widths: [1280], locked: true, login: "wrong", run: (c) => ngLogin(c, { submit: true }) },
  { screen: "ng-login", variant: "locked", data: "default", widths: [1280], locked: true, login: "locked", shells: [{ mode: "light" }], run: (c) => ngLogin(c, { submit: true }) },
  { screen: "ng-firstrun", variant: "step1", data: "empty", widths: [1280], shells: [{ mode: "light" }], run: (c) => ngFirstRun(c, "step1") },
  { screen: "ng-firstrun", variant: "step1", data: "empty", widths: [1024, 1920], run: (c) => ngFirstRun(c, "step1") },
  { screen: "ng-firstrun", variant: "probing", data: "empty", widths: [1280], run: (c) => ngFirstRun(c, "probing") },
  { screen: "ng-firstrun", variant: "probed", data: "empty", widths: [1280], run: (c) => ngFirstRun(c, "probed") },
  { screen: "ng-firstrun", variant: "step2", data: "empty", widths: [1280], shells: [{ mode: "light" }], run: (c) => ngFirstRun(c, "step2") },
  { screen: "ng-firstrun", variant: "step3", data: "empty", widths: [1280], run: (c) => ngFirstRun(c, "step3") },
  ...["graphite", "slate", "ink", "sand", "moss"].map((surface): Case => ({ screen: "ng-tokens", variant: surface, data: "default", widths: [1280], shells: [{ mode: "light" }], fullPage: true, run: (c) => ng(c, "/ng/_tokens", { surface }) })),
  { screen: "ng-tokens", variant: "moss-mono", data: "default", widths: [1280], shells: [{ mode: "light" }], fullPage: true, run: (c) => ng(c, "/ng/_tokens", { surface: "moss", colour_amount: "mono" }) },
  { screen: "ng-tokens", variant: "graphite-violet-full", data: "default", widths: [1920], fullPage: true, run: (c) => ng(c, "/ng/_tokens", { accent: "violet", colour_amount: "full" }) },
  { screen: "ng-appearance", variant: "default", data: "default", widths: [1280, 1024], run: (c) => ng(c, "/ng/settings/appearance", {}) },
  { screen: "ng-appearance", variant: "mono", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => ng(c, "/ng/settings/appearance", { colour_amount: "mono" }) },
  { screen: "ng-appearance", variant: "blue-full", data: "default", widths: [1280], run: (c) => ng(c, "/ng/settings/appearance", { surface: "slate", accent: "blue", colour_amount: "full" }) },
  // An old theme.yaml with only `palette`: GET /theme derives the look (rule A.3).
  { screen: "ng-appearance", variant: "derived", data: "default", widths: [1280], run: (c) => ng(c, "/ng/settings/appearance", { palette: "forest", surface: "moss", accent: "green", colour_amount: "full", derived: true }) },

  // W3: the graph components' gallery, fed by fixtures.
  { screen: "ng-gallery", variant: "default", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], fullPage: true, run: (c) => ng(c, "/ng/_gallery", {}) },
  { screen: "ng-gallery", variant: "mono", data: "default", widths: [1280], fullPage: true, run: (c) => ng(c, "/ng/_gallery", { colour_amount: "mono" }) },
  { screen: "ng-gallery", variant: "full", data: "default", widths: [1280], fullPage: true, run: (c) => ng(c, "/ng/_gallery", { accent: "violet", colour_amount: "full" }) },
  // Under 1024 the pane overlays the canvas (R7).
  { screen: "ng-gallery", variant: "overlay", data: "default", widths: [768], fullPage: true, run: (c) => ng(c, "/ng/_gallery", {}) },
  // R6 by keyboard: Tab lands on the workbench's current node, → moves to the next, Enter opens its pane with the ring on it.
  { screen: "ng-gallery", variant: "keyboard", data: "default", widths: [1280], run: async (c) => {
    await ng(c, "/ng/_gallery", {});
    const bench = c.page.locator('section[aria-labelledby="g-pane"] [role="group"]').first();
    await bench.scrollIntoViewIfNeeded();
    await c.page.keyboard.press("Shift");
    await bench.locator('.graph-node[tabindex="0"]').focus();
    await c.page.keyboard.press("ArrowRight");
    await c.page.keyboard.press("Enter");
    await settle(c.page, 300);
  } },

  // ux2-W6: the board at /ng, its own fixtures (ngBoard.ts).
  { screen: "ng-board", variant: "default", data: "default", widths: [1024, 1280, 1920], shells: [{ mode: "light" }, { short: true }], mock: { ngBoard: true }, run: (c) => ngBoard(c) },
  ...(["long", "many"] as const).map<Case>((d) => ({ screen: "ng-board", variant: d, data: d, widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c) })),
  // No items, but a connected repo: the four empty groups, not first-run.
  { screen: "ng-board", variant: "empty", data: "default", widths: [1280], mock: { ngBoard: "empty" }, run: (c) => ngBoard(c) },
  // The header's and filter bar's menus, opened as a person would.
  ...([["repo-menu", /all repos/], ["chain-menu", /^Chain/], ["group-menu", /^Group/], ["sort-menu", /^Sort/]] as const).map<Case>(([v, name]) => ({
    screen: "ng-board", variant: v, data: "default", widths: [1280], mock: { ngBoard: true },
    run: (c) => ngBoard(c, { then: async (p) => { await p.getByRole("button", { name }).click(); await p.getByRole("menu").waitFor(); } }),
  })),
  // C: no repo connected (first-run), the first read still running, the server gone after boot.
  { screen: "ng-board", variant: "fresh", data: "empty", widths: [1280], mock: { ngBoard: "empty" }, run: (c) => ngBoard(c) },
  { screen: "ng-board", variant: "loading", data: "default", widths: [1280], mock: { ngBoard: true, boardState: "loading" }, run: (c) => ngBoard(c) },
  { screen: "ng-board", variant: "offline", data: "default", widths: [1280], mock: { ngBoard: true, boardState: "offline" }, run: (c) => ngBoard(c, { then: async (p) => { await p.getByText("OFFLINE").waitFor(); } }) },
  // D: selection in every group, the bulk Cancel's inline confirm, and a partial answer.
  { screen: "ng-board", variant: "selected", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { then: checkRows(["Bump the VS Code", "Fix flaky retry", "Remove the legacy poller"]) }) },
  { screen: "ng-board", variant: "bulk-cancel-confirm", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { then: async (p) => {
    await checkRows(["Bump the VS Code", "Fix flaky retry"])(p);
    await p.getByRole("button", { name: "Cancel 2…" }).click();
    await p.getByRole("textbox", { name: /Reason/ }).fill("Superseded by kraft-cb61");
  } }) },
  { screen: "ng-board", variant: "bulk-results", data: "default", widths: [1280], mock: { ngBoard: true, bulkFail: ["kraft-2c77"] }, run: (c) => ngBoard(c, { then: async (p) => {
    await checkRows(["Bump the VS Code", "Lint fan-out"])(p);
    await p.getByRole("button", { name: "‖ Pause 2" }).click();
    await p.getByText("1 of 2 items paused").waitFor();
  } }) },
  // E: the peek on one row of each kind, its other tabs, the overlay under 1024 and a short window.
  ...Object.entries(PEEK_ROWS).map<Case>(([v, title]) => ({
    screen: "ng-board-peek", variant: v, data: "default", widths: [1280], ...(v === "needs-gate" ? { shells: [{ short: true }] } : {}), mock: { ngBoard: true },
    run: (c) => ngBoard(c, { then: peekRow(title) }),
  })),
  ...(["activity", "config"] as const).map<Case>((tab) => ({
    screen: "ng-board-peek", variant: tab, data: "default", widths: [1280], mock: { ngBoard: true },
    run: (c) => ngBoard(c, { then: async (p) => { await peekRow(PEEK_ROWS.capped)(p); await p.getByRole("tab", { name: tab === "activity" ? "Activity" : "Config" }).click(); } }),
  })),
  { screen: "ng-board-peek", variant: "running", data: "default", widths: [768], mock: { ngBoard: true }, run: (c) => ngBoard(c, { side: "rail", then: peekRow(PEEK_ROWS.running) }) },
  // F: the composer at the top of the board, as it fills, its chain menu, the attach search, the discard ask, a refused attachment.
  { screen: "ng-new-item", variant: "composer", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?new=1", then: composerReady }) },
  { screen: "ng-new-item", variant: "filled", data: "default", widths: [1024, 1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?new=1", then: composerFilled }) },
  { screen: "ng-new-item", variant: "chain-menu", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?new=1", then: async (p) => { await composerReady(p); await p.getByRole("button", { name: /^default/ }).click(); await p.getByRole("menu").waitFor(); } }) },
  { screen: "ng-new-item", variant: "attach", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?new=1", then: async (p) => { await composerReady(p); await p.getByRole("button", { name: "+ spec" }).click(); await p.getByRole("textbox", { name: /Search specs/ }).fill("cache"); await p.waitForTimeout(500); } }) },
  { screen: "ng-new-item", variant: "discard", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?new=1", then: async (p) => { await composerFilled(p); await p.keyboard.press("Escape"); await p.getByText(/Discard this draft/).waitFor(); } }) },
  { screen: "ng-new-item", variant: "error", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?new=1", then: async (p) => {
    await composerFilled(p);
    await p.getByRole("button", { name: "+ plan" }).click();
    const box = p.getByRole("textbox", { name: /Search plans/ });
    await box.fill("docs/plans/missing.md"); await p.waitForTimeout(300); await box.press("Enter");
    await p.getByText(/attachment not found/).waitFor();
  } }) },
  // G: the draft item page, empty and titled with a spec, a node and a covered node, Config, YAML, members, the discard ask.
  { screen: "ng-draft-item", variant: "default", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngDraft(c, false) },
  { screen: "ng-draft-item", variant: "titled", data: "default", widths: [1024, 1280], shells: [{ short: true }], mock: { ngBoard: true }, run: (c) => ngDraft(c, true) },
  ...([["node", /^verification,/], ["node-covered", /^spec,/]] as const).map<Case>(([v, name]) => ({
    screen: "ng-draft-item", variant: v, data: "default", widths: [1280], mock: { ngBoard: true },
    run: (c) => ngDraft(c, true, async (p) => { await p.getByRole("button", { name }).click(); }),
  })),
  ...(["Config", "YAML"] as const).map<Case>((tab) => ({
    screen: "ng-draft-item", variant: tab.toLowerCase(), data: "default", widths: [1280], mock: { ngBoard: true },
    run: (c) => ngDraft(c, true, async (p) => { await p.getByRole("tab", { name: new RegExp(`^${tab}`) }).click(); await p.waitForTimeout(400); }),
  })),
  { screen: "ng-draft-item", variant: "members", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngDraft(c, true, async (p) => {
    await p.getByRole("button", { name: "+ members" }).click();
    await p.getByRole("button", { name: /plugins\/kraft-lite/ }).click();
  }) },
  { screen: "ng-draft-item", variant: "discard", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngDraft(c, true, async (p) => { await p.keyboard.press("Escape"); await p.getByRole("alertdialog").waitFor(); }) },
  // H: the archived view, with rows, empty, and two checked.
  { screen: "ng-archived", variant: "default", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "archived" }) },
  { screen: "ng-archived", variant: "empty", data: "default", widths: [1280], mock: { ngBoard: "empty" }, run: (c) => ngBoard(c, { tail: "archived" }) },
  { screen: "ng-archived", variant: "selected", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "archived", then: checkRows(["Trim the default chain", "Drop the v0 webhook"]) }) },
  { screen: "ng-board", variant: "group-repo", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?group=repo" }) },
  { screen: "ng-board", variant: "filtered", data: "default", widths: [1280], mock: { ngBoard: true }, run: (c) => ngBoard(c, { tail: "?q=docs&chain=docs_only" }) },

  // ux2-W5: the item page, every scenario the prototype draws (plus paused), at 1280 and with long data.
  // Three of them also at 1024 and 1920, light and 700px tall.
  ...NG_SCENARIOS.map<Case>((sc) => {
    const wide = ["running", "failed", "needs-gate"].includes(sc);
    return { screen: "ng-item", variant: sc, data: "default", widths: wide ? [1024, 1280, 1920] : [1280], ...(wide ? { shells: [{ mode: "light" }, { short: true }] } : {}), run: (c) => ngItem(c, sc) };
  }),
  // Under 1024 the pane overlays the canvas (R7) and the current node stays clear of it (Kraft-gvfm2).
  { screen: "ng-item", variant: "running", data: "default", widths: [768], run: (c) => ngItem(c, "running", { side: "rail" }) },
  { screen: "ng-item", variant: "chain-config", data: "default", widths: [1280], run: (c) => ngItem(c, "running", { tail: "?tab=config" }) },
  { screen: "ng-item", variant: "chain-config-capped-long", data: "long", widths: [1280], shells: [{ short: true }], run: (c) => ngItem(c, "capped", { tail: "?tab=config" }) },
  // ux2-W5 G: the node view (strip, node canvas, node pane).
  { screen: "ng-item-node", variant: "running", data: "default", widths: [1024, 1280], run: (c) => ngItem(c, "running", { tail: "/nodes/verification" }) },
  { screen: "ng-item-node", variant: "needs-you", data: "default", widths: [1280], run: (c) => ngItem(c, "needs-you", { tail: "/nodes/verification" }) },
  { screen: "ng-item-node", variant: "failed", data: "default", widths: [1280], run: (c) => ngItem(c, "failed", { tail: "/nodes/merge_request" }) },
  // ux2-W5 H: the task pane's tabs, on the attempt the URL names.
  { screen: "ng-item-task", variant: "overview", data: "default", widths: [1280], run: (c) => ngItem(c, "running", { tail: "/nodes/verification?sel=verification.review.code_review" }) },
  { screen: "ng-item-task", variant: "log", data: "default", widths: [1280], shells: [{ short: true }], run: (c) => ngItem(c, "running", { tail: "/nodes/verification?sel=verification.review.code_review&tab=log" }) },
  { screen: "ng-item-task", variant: "log-long", data: "long", widths: [1280], run: (c) => ngItem(c, "running", { tail: "/nodes/verification?sel=verification.checks.lint&tab=log&attempt=1" }) },
  { screen: "ng-item-task", variant: "output", data: "default", widths: [1280], run: (c) => ngItem(c, "running", { tail: "/nodes/verification?sel=verification.checks.lint&tab=output" }) },
  { screen: "ng-item-task", variant: "thread", data: "default", widths: [1280], run: (c) => ngItem(c, "needs-you", { tail: "/nodes/verification?sel=verification.escalation.escalation" }) },
  { screen: "ng-item-step", variant: "parallel", data: "default", widths: [1280], run: (c) => ngItem(c, "running", { tail: "/nodes/verification?sel=verification.checks" }) },
  // ux2-W5 I: the gate's pane (decision card) and its node view.
  { screen: "ng-item-gate", variant: "pane", data: "default", widths: [1280], run: (c) => ngItem(c, "needs-gate", { tail: "?sel=final_review" }) },
  { screen: "ng-item-gate", variant: "view", data: "default", widths: [1024, 1280], run: (c) => ngItem(c, "needs-gate", { tail: "/nodes/final_review" }) },
  { screen: "ng-item-gate", variant: "passed", data: "default", widths: [1280], run: (c) => ngItem(c, "running", { tail: "?sel=plan_approval" }) },
  { screen: "ng-item-gate", variant: "reject", data: "default", widths: [1280], run: (c) => ngItem(c, "needs-gate", { tail: "?sel=final_review", then: async (p) => { await p.getByRole("button", { name: "Reject…" }).click(); } }) },
  // ux2-W11: the item chain draft on the running item (the run stands on verification), seeded by `mock: { itemDraft }`.
  { screen: "ng-item-draft", variant: "changes", data: "default", widths: [1024, 1280, 1920], shells: [{ mode: "light" }], mock: { itemDraft: "changes" }, run: (c) => ngItem(c, "running") },
  { screen: "ng-item-draft", variant: "problems", data: "default", widths: [1024, 1280], mock: { itemDraft: "problems" }, run: (c) => ngItem(c, "running") },
  { screen: "ng-item-draft", variant: "passed", data: "default", widths: [1280], mock: { itemDraft: "passed" }, run: (c) => ngItem(c, "running") },
  { screen: "ng-item-draft", variant: "apply", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], mock: { itemDraft: "changes" }, run: (c) => ngItem(c, "running", { then: async (p) => { await p.getByRole("button", { name: "Review & apply" }).click(); await p.getByRole("dialog").waitFor(); } }) },
  { screen: "ng-item-draft", variant: "apply-blocked", data: "default", widths: [1280], mock: { itemDraft: "problems" }, run: (c) => ngItem(c, "running", { then: async (p) => { await p.getByRole("button", { name: "DRAFT · 1 CHANGE" }).click(); await p.getByRole("dialog").waitFor(); } }) },
  { screen: "ng-item-draft", variant: "seam-menu", data: "default", widths: [1280], mock: { itemDraft: "none" }, run: (c) => ngItem(c, "running", { then: async (p) => { await p.getByRole("button", { name: "Add a library node here" }).first().focus(); await p.keyboard.press("Enter"); await p.getByRole("option", { name: /post_draft_feedback/ }).waitFor(); } }) },
  { screen: "ng-item-draft", variant: "config-edit", data: "default", widths: [1280], mock: { itemDraft: "changes" }, run: (c) => ngItem(c, "running", { tail: "?sel=merge_request.open.open_draft&tab=config", then: async (p) => { await p.getByRole("button", { name: "Override effort" }).click(); } }) },
  { screen: "ng-item-draft", variant: "applied", data: "default", widths: [1280], mock: { itemDraft: "applied" }, run: (c) => ngItem(c, "running", { tail: "?tab=config", then: async (p) => { await p.getByText("applied by the draft").first().waitFor(); } }) },
  { screen: "ng-item-draft", variant: "leave", data: "default", widths: [1280], mock: { itemDraft: "changes" }, run: (c) => ngItem(c, "running", { then: async (p) => { await p.getByRole("link", { name: "Board" }).first().click(); await p.getByRole("dialog").waitFor(); } }) },
  // ux2-W5 J: the document viewer, on the pending gate's document.
  { screen: "ng-item-doc", variant: "artifact", data: "default", widths: [1280], run: (c) => ngItem(c, "needs-gate", { tail: "?sel=final_review", then: async (p) => { await p.getByRole("button", { name: /^Read / }).click(); await p.getByRole("dialog").waitFor(); } }) },
  // The header's floating parts, opened the way a keyboard user would.
  { screen: "ng-item", variant: "panel", data: "default", widths: [1280], run: (c) => ngItem(c, "running", { then: async (p) => { await p.getByRole("button", { name: "More actions" }).focus(); } }) },
  { screen: "ng-item", variant: "kebab", data: "default", widths: [1280], run: (c) => ngItem(c, "running", { then: async (p) => { await p.getByRole("button", { name: "Item menu" }).click(); } }) },
  { screen: "ng-item", variant: "cancel-card", data: "default", widths: [1280], run: (c) => ngItem(c, "mr-closed", { then: async (p) => { await p.getByRole("button", { name: "Item menu" }).click(); await p.getByRole("menuitem", { name: /Cancel/ }).click(); await p.getByText(/stays on the ledger/).waitFor(); } }) },
  ...NG_SCENARIOS.map<Case>((sc) => ({ screen: "ng-item", variant: `${sc}-long`, data: "long", widths: [1280], run: (c) => ngItem(c, sc) })),

  // ux2-W8: the review page and the gate review overlay.
  { screen: "ng-review", variant: "default", data: "default", widths: [1024, 1280, 1920], shells: [{ mode: "light" }, { short: true }], run: (c) => ngReview(c) },
  { screen: "ng-review", variant: "default", data: "default", widths: [768], run: (c) => ngReview(c, { side: "rail" }) },
  { screen: "ng-review", variant: "tree-open", data: "default", widths: [768], run: (c) => ngReview(c, { side: "rail", then: async (p) => { await p.getByRole("button", { name: "Expand file list" }).click(); } }) },
  { screen: "ng-review", variant: "menu-from", data: "default", widths: [1280], run: (c) => ngReview(c, { then: async (p) => { await p.getByRole("button", { name: /^Compare from/ }).click(); } }) },
  { screen: "ng-review", variant: "menu-nodes", data: "default", widths: [1280], run: (c) => ngReview(c, { then: async (p) => { await p.getByRole("button", { name: /^Nodes:/ }).click(); } }) },
  { screen: "ng-review", variant: "menu-settings", data: "default", widths: [1280], run: (c) => ngReview(c, { then: async (p) => { await p.getByRole("button", { name: "Diff settings" }).click(); } }) },
  { screen: "ng-review", variant: "split", data: "default", widths: [1280, 1920], run: (c) => ngReview(c, { settings: ["Side-by-side"] }) },
  { screen: "ng-review", variant: "all-files", data: "default", widths: [1280], run: (c) => ngReview(c, { settings: ["Show one file at a time"] }) },
  { screen: "ng-review", variant: "threads", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => ngReview(c, { settings: ["Show one file at a time"] }) },
  { screen: "ng-review", variant: "composer", data: "default", widths: [1280], run: (c) => ngReview(c, { then: (p) => ngComment(p, "Name the fallback here, so the next reader does not have to find `default=0`.") }) },
  { screen: "ng-review", variant: "suggest", data: "default", widths: [1280], run: (c) => ngReview(c, { then: async (p) => { await ngComment(p, "Say what it returns:"); await p.getByRole("button", { name: "± Suggest change" }).click(); } }) },
  { screen: "ng-review", variant: "finish", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: (c) => ngReview(c, { then: async (p) => { await p.getByRole("button", { name: "Request changes" }).click(); } }) },
  { screen: "ng-review", variant: "finish-gateless", data: "default", widths: [1280], run: (c) => ngReview(c, { sc: "running", then: async (p) => { await p.getByRole("button", { name: "Finish review" }).click(); await p.getByText("Why this node").waitFor(); } }) },
  { screen: "ng-review", variant: "long", data: "long", widths: [1280, 1920], run: (c) => ngReview(c, { settings: ["Show one file at a time"] }) },
  // The gate review overlay: the needs-gate item's document beside its changes (a draft pending, a must-fix open).
  { screen: "ng-gate-review", variant: "default", data: "default", widths: [1024, 1280, 1920], shells: [{ mode: "light" }], run: (c) => ngReview(c, { tail: "?doc=1", then: async (p) => { await p.getByText("WAITING FOR YOU").waitFor(); } }) },
  { screen: "ng-gate-review", variant: "default", data: "default", widths: [768], run: (c) => ngReview(c, { tail: "?doc=1", side: "rail", then: async (p) => { await p.getByText("WAITING FOR YOU").waitFor(); } }) },
  // W10: the Chains editor on the mock's real draft answers (sweep/draftViews.json).
  { screen: "ng-chains", variant: "canvas", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: (c) => ngChains(c, "default") },
  { screen: "ng-chains", variant: "pane-gate", data: "default", widths: [1280], shells: [{ mode: "light" }], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "spec_approval, gate" }).click();
    await settle(c.page, 300);
  } },
  { screen: "ng-chains", variant: "pane-config", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "implementation, node" }).click();
    await c.page.getByRole("tab", { name: "Config" }).click();
    await settle(c.page, 300);
  } },
  { screen: "ng-chains", variant: "node", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: (c) => ngChains(c, "default", "verification") },
  { screen: "ng-chains", variant: "node-task", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default", "verification");
    await c.page.getByRole("button", { name: "code_review, agent task" }).click();
    await settle(c.page, 300);
  } },
  { screen: "ng-chains", variant: "node-empty", data: "default", widths: [1280], run: (c) => ngChains(c, "broken", "lint") },
  { screen: "ng-chains", variant: "gate", data: "default", widths: [1280], run: (c) => ngChains(c, "default", "spec_approval") },
  { screen: "ng-chains", variant: "task-menu", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default", "verification");
    await c.page.getByRole("button", { name: "Add a parallel task" }).first().click();
    await c.page.getByRole("menuitem", { name: "From the library…" }).waitFor({ timeout: 4000 });
    await settle(c.page, 300);
  } },
  { screen: "ng-chains", variant: "bottom", data: "default", widths: [1280], shells: [{ mode: "light" }], run: async (c) => {
    await ngChains(c, "default", "verification");
    await c.page.getByRole("tab", { name: "Fix loop" }).click();
    await settle(c.page, 400);
  } },
  { screen: "ng-chains", variant: "bottom-empty", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default", "verification");
    await c.page.getByRole("tab", { name: "Escalation" }).click();
    await settle(c.page, 400);
  } },
  { screen: "ng-chains", variant: "bottom-handler", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default", "merge_request_feedback");
    await c.page.getByRole("tab", { name: "On failure" }).click();
    await settle(c.page, 400);
  } },
  { screen: "ng-chains", variant: "review", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "Review & publish" }).click();
    await settle(c.page, 500);
  } },
  { screen: "ng-chains", variant: "review-yaml", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "Review & publish" }).click();
    await c.page.getByRole("tab", { name: "YAML diff" }).click();
    await settle(c.page, 500);
  } },
  { screen: "ng-chains", variant: "review-stale", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "stale");
    await c.page.getByRole("button", { name: "Review & publish" }).click();
    await c.page.getByRole("button", { name: "Publish", exact: true }).click();
    await c.page.getByText("Published since this draft began", { exact: true }).waitFor({ timeout: 4000 });
    await settle(c.page, 300);
  } },
  { screen: "ng-chains", variant: "review-problems", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "broken");
    await c.page.getByRole("button", { name: "Review & publish" }).click();
    await settle(c.page, 500);
  } },
  { screen: "ng-chains", variant: "yaml", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "YAML", exact: true }).click();
    await settle(c.page, 500);
  } },
  { screen: "ng-chains", variant: "problems", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "broken");
    await c.page.getByRole("button", { name: "YAML", exact: true }).click();
    await settle(c.page, 500);
  } },
  { screen: "ng-chains", variant: "yaml-error", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "yaml-error");
    await c.page.getByRole("button", { name: "YAML", exact: true }).click();
    await settle(c.page, 500);
  } },
  { screen: "ng-chains", variant: "item-yaml", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "spec_approval, gate" }).click();
    await c.page.getByRole("tab", { name: "YAML" }).click();
    await settle(c.page, 500);
  } },
  { screen: "ng-chains", variant: "switcher", data: "default", widths: [1280], shells: [{ mode: "light" }], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "Chain default, switch chain" }).click();
    await c.page.getByRole("option").first().waitFor({ timeout: 4000 });
    await settle(c.page, 300);
  } },
  { screen: "ng-chains", variant: "unsaved", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "Chain default, switch chain" }).click();
    await c.page.getByRole("option").filter({ hasNotText: /^default/ }).first().click();
    await c.page.getByRole("dialog", { name: "You have unpublished changes" }).waitFor({ timeout: 4000 });
    await settle(c.page, 300);
  } },
  { screen: "ng-chains", variant: "rename", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    const spec = c.page.getByRole("button", { name: "spec, node" });
    await spec.click();
    await spec.click();
    await c.page.getByRole("dialog", { name: "Rename node" }).waitFor({ timeout: 4000 });
    await settle(c.page, 300);
  } },
  { screen: "ng-chains", variant: "remove", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "spec, node" }).click();
    await c.page.getByRole("button", { name: "Remove node" }).click();
    await c.page.getByRole("dialog", { name: "Remove node" }).waitFor({ timeout: 4000 });
    // The pointer off the footer button: its hover tint is W1's (Kraft-xjv0h), not this cell's subject.
    await c.page.mouse.move(400, 600);
    await settle(c.page, 300);
  } },
  { screen: "ng-chains", variant: "reorder", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    const b = (await c.page.getByRole("button", { name: "plan, node" }).boundingBox())!;
    await c.page.mouse.move(b.x + b.width / 2, b.y + 20);
    await c.page.mouse.down();
    await c.page.mouse.move(b.x + b.width / 2 + 120, b.y + 20, { steps: 6 });
    await settle(c.page, 500);
  } },
  { screen: "ng-chains", variant: "change-base", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    // Under the pane at 1280: focus pans it into view, Enter opens its pane.
    await c.page.getByRole("button", { name: "verification, node" }).focus();
    await c.page.keyboard.press("Enter");
    await c.page.getByRole("button", { name: "Change base…" }).click();
    await c.page.getByRole("option").first().click();
    await c.page.getByRole("dialog", { name: /^Change base of/ }).waitFor({ timeout: 4000 });
    await settle(c.page, 300);
  } },
  { screen: "ng-chains", variant: "icon-picker", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "spec, node" }).click();
    await c.page.getByRole("button", { name: /^Icon.*, change$/ }).click();
    await c.page.getByRole("option").first().waitFor({ timeout: 6000 });
    await settle(c.page, 300);
  } },
  { screen: "ng-chains", variant: "canvas-empty", data: "default", widths: [1280], run: (c) => ngChains(c, "empty") },
  { screen: "ng-chains", variant: "seam-menu", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.locator(".seam").nth(3).click();
    await c.page.getByRole("menuitem", { name: /Exec node/ }).waitFor({ timeout: 4000 });
    await settle(c.page, 300);
  } },
  { screen: "ng-chains", variant: "seam-id", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.locator(".seam").nth(3).click();
    await c.page.getByRole("menuitem", { name: /Exec node/ }).click();
    await c.page.keyboard.type("spec");
    await settle(c.page, 300);
  } },

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

/* ── run ─────────────────────────────────────────────────────────────────── */

const only = (env: string | undefined) => (env ? env.split(",").map((s) => s.trim()) : null);
const SCREENS = only(process.env.SWEEP_SCREEN);
const WIDTHS = only(process.env.SWEEP_WIDTHS)?.map(Number);
const VARIANT = only(process.env.SWEEP_VARIANT);

// Every screen gets one ~light cell at 1280: its first case.
const FIRST_OF_SCREEN = new Set<Case>();
{ const seen = new Set<string>(); for (const cs of CASES) if (!seen.has(cs.screen)) { seen.add(cs.screen); FIRST_OF_SCREEN.add(cs); } }

for (const cs of CASES) {
  if (SCREENS && !SCREENS.some((s) => cs.screen.startsWith(s))) continue;
  if (VARIANT && !VARIANT.some((v) => cs.variant.startsWith(v))) continue;
  const shells: Shell[] = [{}, ...(cs.shells ?? [])];
  const combos: [number, Shell][] = [];
  for (const width of cs.widths) for (const shell of shells) {
    // Shell variants only run at 1280 unless the case pins its own widths.
    if (Object.keys(shell).length && !cs.shells?.length) continue;
    if (Object.keys(shell).length && width !== 1280 && cs.shells === SHELLS_1280) continue;
    combos.push([width, shell]);
  }
  if (FIRST_OF_SCREEN.has(cs) && !combos.some(([w, s]) => w === 1280 && s.mode === "light")) combos.push([1280, { mode: "light" }]);
  if (cs.firstpaint) combos.push([1280, { mode: "light", firstpaint: true }]);
  for (const [width, shell] of combos) {
    if (WIDTHS && !WIDTHS.includes(width)) continue;
    {
      const id = `${cs.screen}/${cs.variant}@${width}${shellId(shell) === "auto" ? "" : "~" + shellId(shell)}`;
      test(id, async ({ page }, testInfo) => {
        const [w, h0] = VP[width];
        const h = shell.short ? 700 : h0;
        await page.setViewportSize({ width: w, height: h });
        const S = buildScenario(cs.data, { mode: shell.mode, density: shell.density, group_by: shell.group_by });
        await installMocks(page, S, { locked: cs.locked, login: cs.login, ...cs.mock });
        await page.addInitScript((sb) => {
          if (sb) localStorage.setItem("kraft.sidebar_collapsed", sb === "rail" ? "true" : "false");
          else localStorage.removeItem("kraft.sidebar_collapsed");
        }, shell.sidebar ?? "");
        // A locked page cannot read /api/theme, so the mode shell reaches it the
        // only way a real one does: the theme this browser saved last session.
        if (cs.locked && shell.mode) await page.addInitScript((mode) => { localStorage.setItem("kraft.theme", JSON.stringify({ palette: "nocturne", mode })); localStorage.setItem("kraft.theme.v2", JSON.stringify({ surface: "graphite", mode })); }, shell.mode);
        const consoleErrors: string[] = [];
        page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 300)); });
        page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message.slice(0, 300)}`));

        let setupError: string | undefined;
        let scrolledChrome: Awaited<ReturnType<typeof chromeRects>> | null = null;
        let before: Awaited<ReturnType<typeof chromeRects>> | null = null;
        try {
          await cs.run({ page, S, width, shell });
          // A returning visitor's first paint: the app has run once in this
          // browser, so whatever it persists is there; shoot before React mounts.
          // /api/theme is held back on that reload so the frame is the one before
          // React mounts (a slow server), not the finished page.
          if (shell.firstpaint) {
            await page.route(/\/api\/theme$/, async (r) => { await new Promise((res) => setTimeout(res, 1500)); await r.fallback(); });
            await page.reload({ waitUntil: "domcontentloaded" });
          }
          if (cs.variant.includes("scrolled")) { before = await chromeRects(page); await scrollAllToBottom(page); scrolledChrome = await chromeRects(page); }
        } catch (e) {
          setupError = e instanceof Error ? e.message.split("\n")[0].slice(0, 300) : String(e);
        }
        const file = `${cs.screen}/${cs.variant}@${width}${shellId(shell) === "auto" ? "" : "~" + shellId(shell)}.png`;
        fs.mkdirSync(path.join(OUT, cs.screen), { recursive: true });
        await page.screenshot({ path: path.join(OUT, file), fullPage: cs.fullPage ?? false, animations: "disabled", caret: "hide" }).catch(() => {});
        const checks: Checks = await runChecks(page, width < 768, consoleErrors).catch((e) => ({
          pageOverflowX: false, offscreenRight: { count: 0, examples: [] }, clippedEllipsis: { count: 0, examples: [] }, clippedVertical: { count: 0, examples: [] },
          smallTargets: { count: 0, examples: [] }, smallInputs: { count: 0, examples: [] }, nestedScrollers: { count: 0, examples: [] },
          negativeDurations: { count: 0, examples: [] }, lowContrast: { count: 0, examples: [] }, consoleErrors, setupError: `checks failed: ${e}`,
        }));
        if (setupError) checks.setupError = setupError;
        const chromeMoved = before && scrolledChrome ? Object.keys(before).filter((k) => (before as any)[k] !== null && (before as any)[k] !== (scrolledChrome as any)[k]) : [];
        const entry = {
          id, screen: cs.screen, variant: cs.variant, data: cs.data, width: w, height: h, shell: shellId(shell), file,
          url: page.url(), phone: width < 768, chromeMoved, checks, at: new Date().toISOString(),
        };
        fs.appendFileSync(MANIFEST, JSON.stringify(entry) + "\n");
        testInfo.annotations.push({ type: "sweep", description: file });
      });
    }
  }
}
