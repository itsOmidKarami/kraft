import { expect, type Page } from "@playwright/test";
import type { Scenario } from "./mocks/fixtures";
import { lightMode } from "./mocks/fixtures";
import { app, away, contract, dragPaneEdge, focusedName, pause, type Row } from "./kit";

/** The shell: the sidebar's pin, reveal and persistence, search, the board's side panel, settings navigation, tooltips. */

const searchRow = (p: Page) => p.getByRole("button", { name: /^Search/ }).first();
/** How far right the sidebar reaches: ~210 open, ~0 unpinned (it takes no width). */
const sideEdge = async (p: Page) => { const b = await searchRow(p).boundingBox(); return b ? b.x + b.width : 0; };
const OPEN = 150;
const tipOf = (p: Page) => p.locator('[role="tooltip"]').first();

const appToggle = (p: Page) => p.getByRole("button", { name: "Pin sidebar" }).click();
/** Unpin from the pinned default, then move the pointer off so the sidebar hides. */
const appUnpin = async (p: Page) => { await appToggle(p); await away(p); };
/** Hover the pin in the top-left corner, then move down into the panel it revealed. */
const reveal = async (p: Page) => { await p.getByRole("button", { name: "Pin sidebar" }).hover(); await pause(p); await p.mouse.move(100, 420, { steps: 4 }); await pause(p, 300); };
/** Unpinned and revealed by hovering its pin. */
const appRevealed = async (p: Page, tweak?: (S: Scenario) => void) => {
  await app(p, "/", { tweak });
  await appUnpin(p);
  await reveal(p);
  await pause(p, 100);
};
/** The first box-shadow found on the element holding the Search row or one of its first six ancestors. */
const shadowAround = (p: Page) => searchRow(p).evaluate((el) => {
  let n: HTMLElement | null = el as HTMLElement;
  for (let i = 0; n && i < 7; i++, n = n.parentElement) { const s = getComputedStyle(n).boxShadow; if (s && s !== "none") return s; }
  return "none";
});

const ROWS: Row[] = [
  // Sidebar
  {
    name: "sidebar: open by default on a 1280px window",
    run: async (p) => { await app(p, "/"); expect(await sideEdge(p)).toBeGreaterThan(OPEN); },
  },
  {
    // A page with no pane to open: the Board is the exception, below.
    name: "sidebar: open by default on a 900px window",
    run: async (p) => { await p.setViewportSize({ width: 900, height: 800 }); await app(p, "/analytics"); expect(await sideEdge(p)).toBeGreaterThan(OPEN); },
  },
  {
    name: "sidebar: the Board steps back to its rail on a 900px window, so its peek moves nothing",
    run: async (p) => { await p.setViewportSize({ width: 900, height: 800 }); await app(p, "/"); expect(await sideEdge(p)).toBeLessThan(OPEN); },
  },
  {
    name: "sidebar: the toggle collapses it and the pointer leaving keeps it collapsed",
    run: async (p) => { await app(p, "/"); await appToggle(p); await away(p); expect(await sideEdge(p)).toBeLessThan(OPEN); },
  },
  {
    name: "sidebar: unpinned, it takes no width (no icon rail) [decided]",
    run: async (p) => { await app(p, "/"); await appUnpin(p); expect(await sideEdge(p)).toBeLessThan(20); expect((await p.locator("main").first().boundingBox())!.x).toBeLessThan(20); },
  },
  {
    name: "sidebar: collapsed, hovering its pin reveals it and the window's left edge does not [decided]",
    run: async (p) => {
      await app(p, "/"); await appToggle(p); await away(p);
      await p.mouse.move(3, 420); await pause(p); expect(await sideEdge(p)).toBeLessThan(OPEN);
      await p.getByRole("button", { name: "Pin sidebar" }).hover(); await pause(p); expect(await sideEdge(p)).toBeGreaterThan(OPEN);
    },
  },
  {
    name: "sidebar: unpinned, keyboard focus on one of its controls reveals it",
    run: async (p) => { await app(p, "/", { side: "rail" }); await away(p); expect(await sideEdge(p)).toBeLessThan(OPEN); await p.keyboard.press("Tab"); await p.keyboard.press("Tab"); await pause(p, 300); expect(await sideEdge(p)).toBeGreaterThan(OPEN); },
  },
  {
    name: "sidebar: a revealed sidebar hides again once the pointer leaves",
    run: async (p) => { await app(p, "/"); await appUnpin(p); await reveal(p); await away(p); expect(await sideEdge(p)).toBeLessThan(OPEN); },
  },
  {
    name: "sidebar: a revealed sidebar stays open on Esc while the pointer is over it [decided]",
    run: async (p) => { await app(p, "/"); await appUnpin(p); await reveal(p); await p.keyboard.press("Escape"); await pause(p); expect(await sideEdge(p)).toBeGreaterThan(OPEN); },
  },
  {
    name: "sidebar: unpinning with the pointer over it keeps it open until the pointer leaves [decided]",
    run: async (p) => { await app(p, "/"); await appToggle(p); await pause(p, 600); expect(await sideEdge(p)).toBeGreaterThan(OPEN); await away(p); expect(await sideEdge(p)).toBeLessThan(OPEN); },
  },
  {
    name: "sidebar: unpinned and revealed, choosing a row keeps it open while the pointer stays [decided]",
    run: async (p) => { await app(p, "/"); await appUnpin(p); await reveal(p); await p.getByRole("link", { name: /^Analytics/ }).first().click(); await pause(p, 600); expect(await sideEdge(p)).toBeGreaterThan(OPEN); },
  },
  {
    name: "sidebar: collapsing persists across a reload",
    run: async (p) => { await app(p, "/"); await appToggle(p); await away(p); await p.reload(); await p.locator(".ng-sidebar").waitFor(); await pause(p, 800); expect(await sideEdge(p)).toBeLessThan(OPEN); },
  },
  {
    name: "sidebar: pinning again persists across a reload",
    run: async (p) => { await app(p, "/"); await appUnpin(p); await reveal(p); await appToggle(p); await away(p); await p.reload(); await p.locator(".ng-sidebar").waitFor(); await pause(p, 800); expect(await sideEdge(p)).toBeGreaterThan(OPEN); },
  },
  {
    name: "sidebar: ⌘\\ / Ctrl+\\ toggles it",
    run: async (p) => { await app(p, "/"); await p.keyboard.press("ControlOrMeta+Backslash"); await away(p); expect(await sideEdge(p)).toBeLessThan(OPEN); },
  },
  {
    name: "sidebar: the footer shows the version and an update notice that opens About [decided]",
    run: async (p) => { await app(p, "/"); const f = p.getByRole("link", { name: /^Kraft v\d.*update available/ }); await expect(f).toBeVisible({ timeout: 4000 }); await expect(f).toContainText("update"); await f.click(); await pause(p); expect(p.url()).toContain("/settings/about"); },
  },
  {
    name: "sidebar: the pin sits in the top-left corner, in the same place pinned and collapsed [decided]",
    run: async (p) => {
      await app(p, "/"); const pin = p.getByRole("button", { name: "Pin sidebar" });
      const a = (await pin.boundingBox())!; expect(a.y).toBeLessThan(44); expect(a.x).toBeLessThan(20);
      await appUnpin(p); expect(await pin.boundingBox()).toEqual(a);
    },
  },

  // Search
  {
    name: "search: ⌘K opens it, Esc closes it",
    run: async (p) => { await app(p, "/"); await p.keyboard.press("ControlOrMeta+k"); const box = p.getByRole("combobox", { name: /search/i }); await expect(box).toBeFocused(); await p.keyboard.press("Escape"); await pause(p); await expect(box).toBeHidden(); },
  },
  {
    name: "search: ⌘K a second time keeps it open [decided: accepted]",
    run: async (p) => { await app(p, "/"); await p.keyboard.press("ControlOrMeta+k"); await pause(p, 300); await p.keyboard.press("ControlOrMeta+k"); await pause(p); await expect(p.getByRole("combobox", { name: /search/i })).toBeVisible(); },
  },
  {
    name: "search: a needs-you row for a gate is an action row, Review <gate>, and Enter opens that review (Q1)",
    run: async (p) => { await app(p, "/"); await p.keyboard.press("ControlOrMeta+k"); await pause(p, 400); const r = p.getByRole("option", { name: /Review · approve final review/ }).first(); await expect(r).toBeVisible({ timeout: 3000 }); await r.click(); await pause(p, 800); expect(p.url()).toMatch(/\/review/); },
  },
  {
    name: "search: the query is highlighted in titles (Q3)",
    run: async (p) => { await app(p, "/"); await p.keyboard.press("ControlOrMeta+k"); await p.getByRole("combobox", { name: /search/i }).fill("measured"); await pause(p, 600); expect(await p.getByRole("option").locator("mark").filter({ hasText: /^measured$/i }).count(), "the app's fixture titles hold \"measured\", not \"cache\"").toBeGreaterThan(0); },
  },
  {
    name: "search: Filters sits at the right end of the tab row (Q5)",
    run: async (p) => { await app(p, "/"); await p.keyboard.press("ControlOrMeta+k"); await p.getByRole("combobox", { name: /search/i }).fill("cache"); await pause(p, 500); const f = (await p.getByRole("button", { name: /^Filters/ }).first().boundingBox())!; const box = (await p.getByRole("combobox", { name: /search/i }).boundingBox())!; expect(f.x + f.width).toBeGreaterThan(box.x + box.width - 60); },
  },
  {
    name: "search: closing it returns focus to where it was opened from",
    run: async (p) => { await app(p, "/"); const b = p.getByRole("button", { name: /New work item/ }).first(); await b.focus(); await p.keyboard.press("ControlOrMeta+k"); await pause(p, 300); await p.keyboard.press("Escape"); await pause(p, 300); await expect(b).toBeFocused(); },
  },

  // Board
  {
    name: "board: clicking a row opens its side panel; Esc closes it",
    run: async (p) => { await app(p, "/"); await p.getByRole("button", { name: /^Design the caching layer/ }).first().click(); await p.locator(".pane .pane-tabs").waitFor(); await p.keyboard.press("Escape"); await pause(p); await expect(p.locator(".pane .pane-tabs")).toBeHidden(); },
  },
  {
    name: "board: closing the side panel with Esc returns focus to its row",
    run: async (p) => { await app(p, "/"); await p.getByRole("button", { name: /^Design the caching layer/ }).first().click(); await p.locator(".pane .pane-tabs").waitFor(); await p.keyboard.press("Escape"); await pause(p); expect(await focusedName(p)).toMatch(/Design the caching layer/); },
  },
  {
    name: "board: the side panel's width, once dragged, persists across a reload",
    run: async (p) => { await app(p, "/"); await p.getByRole("button", { name: /^Design the caching layer/ }).first().click(); await p.locator(".pane .pane-tabs").waitFor(); const h = p.getByRole("separator", { name: "Resize pane" }); const [x0, x1, x2] = await dragPaneEdge(p, h, async () => { await p.reload(); await p.locator(".pane .pane-tabs").waitFor(); await pause(p); }); expect(x1).toBeLessThan(x0 - 40); expect(Math.abs(x2 - x1)).toBeLessThan(4); },
  },

  // Settings
  {
    name: "settings: each settings page is reachable from the sidebar (About from the footer) [decided]",
    run: async (p) => { await app(p, "/"); for (const l of ["Policy", "Auto-intake", "Notifications", "Access", "Appearance"]) { await p.getByRole("link", { name: new RegExp(`^${l}`) }).first().click(); await pause(p, 300); } await p.getByRole("link", { name: /^Kraft v\d/ }).click({ timeout: 2000 }); await pause(p, 300); expect(p.url()).toContain("/settings/about"); },
  },
  {
    name: "settings: an address under the retired /ng prefix opens its page, query kept",
    run: async (p) => { const S = await app(p, (S) => `/ng/work-items/${S.ng.running}?sel=verification`); expect(new URL(p.url()).pathname + new URL(p.url()).search).toBe(`/work-items/${S.ng.running}?sel=verification`); await expect(p.getByRole("complementary", { name: "verification pane" })).toBeVisible(); },
  },
  {
    name: "settings: a settings page has its own URL (reload stays on it)",
    run: async (p) => { await app(p, "/settings/appearance"); await p.reload(); await p.locator("main h1").first().waitFor(); expect(p.url()).toContain("/settings/appearance"); },
  },

  {
    name: "search: an empty search lists Go to page chips below Recent (SR-3)",
    run: async (p) => { await app(p, "/"); await p.keyboard.press("ControlOrMeta+k"); await pause(p, 600); expect(await p.getByText("Go to", { exact: true }).count()).toBeGreaterThan(0); },
  },
  // Decisions since sweep 2
  {
    name: "board: a spend- or time-capped row offers Raise cap / Raise budget [decided]",
    run: async (p) => { await app(p, "/"); await expect(p.getByRole("button", { name: /^Raise (cap|budget)/ }).first()).toBeVisible({ timeout: 3000 }); },
  },
  {
    name: "phone board: a capped row offers Raise cap [decided]",
    run: async (p) => { await p.setViewportSize({ width: 390, height: 844 }); await app(p, "/", { noShell: true }); await expect(p.getByRole("button", { name: /Raise (cap|budget)/ }).first()).toBeVisible({ timeout: 3000 }); },
  },
  {
    name: "board: on a 1024px window \"in your checkout\" gives its room to a tail that already says the checkout, and stays on a running row",
    run: async (p) => {
      await p.setViewportSize({ width: 1024, height: 768 });
      await app(p, "/", { data: "long" });
      const inPlace = (title: string) => p.locator(".board-row").filter({ has: p.locator(".board-title", { hasText: title }) }).locator(".board-meta-inplace");
      await expect(inPlace("Cache the search index in place")).toBeHidden();
      await expect(inPlace("Tidy the extension's settings page")).toBeHidden();
      await expect(inPlace("Bump the VS Code extension to schema v4")).toBeVisible();
    },
  },
  {
    name: "board: on a 1024px window a long repo name gives the reason room and a short one stays whole, and a wide window keeps a long reason on one line",
    run: async (p) => {
      await p.setViewportSize({ width: 1024, height: 768 });
      const row = (title: string) => p.locator(".board-row").filter({ has: p.locator(".board-title", { hasText: title }) });
      const tail = (title: string) => row(title).locator(".board-tail-text");
      await app(p, "/");
      // "kraft-api" beside a long reason: once cut to "kr…" when the crumb shrank with the tail.
      const cut = await row("Add rate limit headers").locator(".board-meta-repo").evaluate((el) => el.scrollWidth > el.clientWidth);
      expect(cut, "a short repo name is cut").toBe(false);
      await app(p, "/", { data: "long" });
      // Before the repo crumb was capped: 79px and 176px.
      expect((await tail("Bump the VS Code extension").boundingBox())!.width).toBeGreaterThan(100);
      expect((await tail("Cache the search index in place").boundingBox())!.width).toBeGreaterThan(250);
      await p.setViewportSize({ width: 1440, height: 900 });
      await pause(p, 300);
      // A path's break points are the narrow row's only: one line here, not five.
      expect((await tail("Cache the search index in place").boundingBox())!.height).toBeLessThan(30);
    },
  },
  {
    name: "phone board: an in-place card never splits \"in your checkout\" across lines",
    run: async (p) => {
      await p.setViewportSize({ width: 390, height: 844 });
      await app(p, "/", { noShell: true, data: "long" });
      const lines = await p.locator(".ph-card").filter({ hasText: "Cache the search index in place" }).getByText("in your checkout").evaluate((el) => el.getClientRects().length);
      expect(lines).toBe(1);
    },
  },
  {
    name: "phone item: an in-place item's meta line never splits \"in your checkout\" across lines",
    run: async (p) => {
      await p.setViewportSize({ width: 320, height: 700 });
      await app(p, (S) => `/work-items/${Object.values(S.bundles).find((b) => b.item.title.startsWith("Tidy the extension's settings page"))!.item.id}`, { noShell: true });
      const lines = await p.locator(".ph-item-meta").getByText("in your checkout").evaluate((el) => el.getClientRects().length);
      expect(lines).toBe(1);
    },
  },
  {
    name: "sidebar: a revealed (unpinned) sidebar floats over the page with an overlay shadow [decided]",
    run: async (p) => { await appRevealed(p); expect(await shadowAround(p)).not.toBe("none"); },
  },
  {
    name: "sidebar: in light mode the revealed sidebar still has a visible shadow [decided]",
    run: async (p) => { await appRevealed(p, lightMode); await expect(p.locator("html")).toHaveAttribute("data-mode", "light"); const s = await shadowAround(p); expect(s).not.toBe("none"); expect(s, "an rgba with alpha > 0.05").toMatch(/rgba?\(/); },
  },
  {
    name: "tooltips: an icon-only button shows its label after hovering it [decided]",
    run: async (p) => {
      await app(p, "/");
      await p.getByRole("button", { name: "Pin sidebar" }).hover(); await pause(p, 700);
      await expect(tipOf(p)).toHaveText(/sidebar/i);
    },
  },
  {
    name: "tooltips: …and when it holds keyboard focus",
    run: async (p) => {
      await app(p, "/");
      await p.keyboard.press("Tab"); await p.getByRole("button", { name: "Pin sidebar" }).focus(); await pause(p, 700);
      await expect(tipOf(p)).toHaveText(/sidebar/i);
    },
  },
  {
    name: "tooltips: in light mode the tooltip is readable (text and background differ)",
    run: async (p) => {
      await app(p, "/", { tweak: lightMode }); await expect(p.locator("html")).toHaveAttribute("data-mode", "light");
      await p.getByRole("button", { name: "Pin sidebar" }).hover(); await pause(p, 700);
      const t = tipOf(p); await expect(t).toBeVisible();
      const [fg, bg] = await t.evaluate((e) => { const c = getComputedStyle(e); return [c.color, c.backgroundColor]; });
      expect(fg).not.toBe(bg);
    },
  },  {
    name: "settings: About's Address row says whether sign-in is on, and its footer names the Python the server runs [decided]",
    run: async (p) => {
      await app(p, "/settings/about");
      await expect(p.getByText(/127\.0\.0\.1:8765 · sign-in (on|off on localhost)/)).toBeVisible({ timeout: 3000 });
      await expect(p.getByText(/Python 3\.14/)).toBeVisible();
    },
  },
  {
    name: "offline: a page that loads its own data says the server is down and can retry, rather than loading forever [decided]",
    run: async (p) => {
      await app(p, "/settings/policy", { mock: { boardState: "offline" }, ready: (pg) => pg.getByRole("button", { name: "Retry now" }).waitFor() });
      await expect(p.getByText(/^Offline: /)).toBeVisible();
      await expect(p.getByText("Loading policy…")).toHaveCount(0);
      await expect(p.getByRole("heading", { name: "Could not load policy" })).toBeVisible();
    },
  },
];

contract(ROWS);
