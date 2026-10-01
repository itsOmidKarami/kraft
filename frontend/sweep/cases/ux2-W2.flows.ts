import { expect, type Page } from "@playwright/test";
import { ng, settle, type Flow } from "../flowKit";

const sideWidth = async (p: Page) => (await p.locator(".ng-sidebar").boundingBox())!.width;
const sideIs = async (p: Page, mode: "pinned" | "rail") => {
  expect(await p.evaluate(() => document.documentElement.dataset.sidebar)).toBe(mode);
  // The width animates; poll until it settles.
  if (mode === "pinned") await expect.poll(() => sideWidth(p)).toBeGreaterThan(150); else await expect.poll(() => sideWidth(p)).toBeLessThan(100);
};
const reloadNg = async (p: Page) => { await p.reload(); await p.locator("main h1").first().waitFor({ timeout: 8000 }); await settle(p, 500); };
let focusBefore = "";
const activeId = (p: Page) => p.evaluate(() => { const a = document.activeElement as HTMLElement; return a ? `${a.tagName}#${a.id}.${a.className}` : ""; });
const searchBox = (p: Page) => p.getByRole("combobox", { name: "Search" });

export const flows: Flow[] = [
  { name: "ng-search-keyboard", widths: [1280], start: ng("/ng/settings/policy"), steps: [
    { name: "open", run: async (p) => { focusBefore = await activeId(p); await p.keyboard.press("Control+k"); await expect(searchBox(p)).toBeVisible(); await expect(searchBox(p)).toBeFocused(); }, kbd: true },
    { name: "type", run: async (p) => { await searchBox(p).fill("gate"); await p.waitForTimeout(700); await expect(p.getByRole("option").first()).toBeVisible(); }, kbd: true, wait: 200 },
    { name: "down-down-up", run: async (p) => {
      const at = () => searchBox(p).getAttribute("aria-activedescendant");
      const start = await at();
      await p.keyboard.press("ArrowDown"); const one = await at();
      await p.keyboard.press("ArrowDown"); const two = await at();
      await p.keyboard.press("ArrowUp"); const back = await at();
      expect(new Set([start, one, two]).size).toBe(3);
      expect(back).toBe(one);
      await expect(searchBox(p)).toBeFocused();
    }, kbd: true },
    { name: "escape-returns-focus", run: async (p) => { await p.keyboard.press("Escape"); await expect(searchBox(p)).toHaveCount(0); expect(await activeId(p)).toBe(focusBefore); }, kbd: true },
    { name: "reopen-enter-goes", run: async (p) => {
      const url = p.url();
      await p.keyboard.press("Control+k"); await searchBox(p).fill("analytics"); await p.waitForTimeout(500);
      await p.keyboard.press("Enter");
      await expect(searchBox(p)).toHaveCount(0);
      await expect.poll(() => p.url()).not.toBe(url);
    }, kbd: true, wait: 700 },
  ] },
  { name: "ng-sidebar-pin", widths: [1280], start: ng("/ng/settings/policy"), steps: [
    { name: "default-pinned", run: async (p) => { await sideIs(p, "pinned"); } },
    { name: "unpin-with-shortcut", run: async (p) => { await p.keyboard.press("Control+\\"); await sideIs(p, "rail"); }, kbd: true },
    { name: "reload-stays-rail", run: async (p) => { await reloadNg(p); await sideIs(p, "rail"); } },
    { name: "pin-with-shortcut", run: async (p) => { await p.keyboard.press("Control+\\"); await sideIs(p, "pinned"); }, kbd: true },
    { name: "reload-stays-pinned", run: async (p) => { await reloadNg(p); await sideIs(p, "pinned"); } },
  ] },
  { name: "ng-sidebar-rail", widths: [1024], start: ng("/ng/settings/policy"), steps: [
    { name: "rail-by-default", run: async (p) => { await sideIs(p, "rail"); } },
    { name: "hover-reveals", run: async (p) => { await p.mouse.move(20, 300); await expect.poll(() => sideWidth(p)).toBeGreaterThan(150); } },
    { name: "leaving-collapses", run: async (p) => { await p.mouse.move(700, 450); await expect.poll(() => sideWidth(p)).toBeLessThan(100); } },
    { name: "focus-reveals", run: async (p) => { await p.getByRole("button", { name: "Search" }).first().focus(); await expect.poll(() => sideWidth(p)).toBeGreaterThan(150); }, kbd: true },
  ] },
];
