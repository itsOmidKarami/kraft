import type { Page } from "@playwright/test";
import type { DisplayState, Scenario, Variant } from "./fixtures";
import type { MockOptions } from "./mockApi";
import { NG_NOW } from "./ngItems";

/** What every sweep/cases/<wave>.ts imports; sweep.spec.ts imports it too. */

export const ALL = [390, 768, 1024, 1100, 1280, 1440, 1920];
export const KEY = [390, 1100, 1280, 1920];
// A desktop browser with a side panel open (W10.D): the peek and the item page just under 1024.
export const SIDE = [960, 1000];

export interface Shell { sidebar?: "open" | "rail"; mode?: "light" | "dark"; density?: "compact" | "comfortable"; group_by?: "repo" | "template"; short?: boolean; firstpaint?: boolean }
export const SHELLS_1280: Shell[] = [{ sidebar: "open" }, { sidebar: "rail" }, { mode: "light" }, { density: "comfortable" }, { short: true }];
export const shellId = (s: Shell) => [s.sidebar, s.mode, s.density, s.group_by, s.short ? "h700" : "", s.firstpaint ? "firstpaint" : ""].filter(Boolean).join("-") || "auto";

export interface Ctx { page: Page; S: Scenario; width: number; shell: Shell }
export interface Case {
  screen: string;
  variant: string;
  data: Variant;
  widths: number[];
  shells?: Shell[];
  locked?: boolean;
  /** With `locked`: what the mock's POST /login answers. */
  login?: "ok" | "wrong" | "locked";
  fullPage?: boolean;
  /** Not the screen's first-case ~light cell at 1280: a phone-only screen has no 1280 shape (ux2-W17). */
  noLight?: true;
  /** Also shoot `~light-firstpaint` at 1280: reload and screenshot at DOMContentLoaded, 0ms settle. */
  firstpaint?: boolean;
  /** More of the mock's options (ux2-W6: the board's fixtures and states). */
  mock?: MockOptions;
  run: (c: Ctx) => Promise<void>;
}

export const settle = (page: Page, ms = 400) => page.waitForTimeout(ms);
export const idOf = (S: Scenario, st: DisplayState) => S.byState[st].item.id;
/** The Chains editor on one of the mock's drafts, sidebar pinned, once the canvas has drawn. */
export async function ngChains(c: Ctx, key: string, node?: string) {
  await c.page.addInitScript(() => localStorage.setItem("kraft.sidebar.v2", "pinned"));
  await c.page.goto(`/templates/chains/${key}${node ? `/nodes/${node}` : ""}`);
  // A node view draws no canvas: wait for its pane's heading instead.
  if (node) await c.page.getByRole("heading", { level: 2, name: node, exact: true }).waitFor({ timeout: 8000 });
  else await c.page.locator(".canvas, .tpl-note").first().waitFor({ timeout: 8000 });
  await settle(c.page, 700);
}
/** A page under a given look: the mock's theme is what GET /theme answers. */
export async function ng(c: Ctx, url: string, look: Record<string, unknown>, opts: { side?: "pinned" | "rail"; hover?: boolean } = {}) {
  Object.assign(c.S.settings.theme, look);
  if (opts.side) await c.page.addInitScript((v) => localStorage.setItem("kraft.sidebar.v2", v), opts.side);
  await c.page.goto(url);
  await c.page.locator("main h1").first().waitFor({ timeout: 8000 });
  await settle(c.page, 600);
  if (opts.hover) { await c.page.mouse.move(20, 300); await settle(c.page, 500); }
}
/** The item page (ux2-W5) for one of ngItems.ts's scenarios, the clock fixed at NG_NOW so elapsed reads the same every run. */
export async function ngItem(c: Ctx, sc: string, opts: { tail?: string; side?: "pinned" | "rail"; then?: (p: Page) => Promise<void> } = {}) {
  await c.page.clock.setFixedTime(new Date(NG_NOW));
  await ng(c, `/work-items/${c.S.ng[sc]}${opts.tail ?? ""}`, {}, { side: opts.side ?? "pinned" });
  if (opts.then) { await opts.then(c.page); await settle(c.page, 400); }
}
