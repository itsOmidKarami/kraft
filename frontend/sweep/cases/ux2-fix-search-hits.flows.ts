import { expect } from "@playwright/test";
import { searchFor } from "../fixtures";
import { ng, type Flow } from "../flowKit";

const activeId = (p: import("@playwright/test").Page) => p.evaluate(() => { const a = document.activeElement as HTMLElement; return a ? `${a.tagName}#${a.id}.${a.className}` : ""; });
let before = "";

export const flows: Flow[] = [
  // ⌘K -> a document hit with a work item -> the item page with the document open -> Esc closes it, the page stays.
  { name: "search-doc-keyboard", widths: [1280], keyboard: true, start: ng("/settings/policy"), steps: [
    { name: "open-and-type", run: async (p) => { before = await activeId(p); await p.keyboard.press("Control+k"); await p.getByRole("combobox", { name: "Search" }).fill("cache"); await expect(p.getByRole("option").first()).toBeVisible(); }, kbd: true, wait: 700 },
    { name: "enter-opens-the-item-with-the-doc", run: async (p) => {
      await p.getByRole("listbox").locator('[data-section="docs"] [role="option"]').first().hover();
      await p.keyboard.press("Enter");
      await expect.poll(() => new URL(p.url()).searchParams.get("doc")).not.toBeNull();
      await expect(p.getByRole("dialog")).toBeVisible();
    }, kbd: true, wait: 600 },
    { name: "escape-drops-the-doc", run: async (p) => { await p.keyboard.press("Escape"); await expect(p.getByRole("dialog")).toHaveCount(0); expect(new URL(p.url()).searchParams.has("doc")).toBe(false); }, kbd: true },
  ] },
  // ⌘K -> a document hit with no item -> the dialog; Esc returns focus to where ⌘K was opened.
  { name: "search-free-doc-focus", widths: [1280], keyboard: true, start: ng("/settings/policy"), steps: [
    { name: "open-the-dialog", run: async (p, S) => {
      const found = searchFor("cache", Object.values(S.docs).flat(), S.variant);
      await p.route("**/api/search*", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...found, results: found.results.map((x) => ({ ...x, links: [] })) }) }));
      before = await activeId(p);
      await p.keyboard.press("Control+k"); await p.getByRole("combobox", { name: "Search" }).fill("cache"); await p.waitForTimeout(700);
      await p.getByRole("listbox").locator('[data-section="docs"] [role="option"]').first().click();
      await expect(p.getByRole("dialog")).toBeVisible();
      await expect(p.getByRole("combobox", { name: "Search" })).toHaveCount(0);
    }, kbd: true, wait: 400 },
    { name: "escape-returns-focus", run: async (p) => { await p.keyboard.press("Escape"); await expect(p.getByRole("dialog")).toHaveCount(0); expect(await activeId(p)).toBe(before); }, kbd: true },
  ] },
];
