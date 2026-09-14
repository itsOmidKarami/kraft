import { expect, test } from "@playwright/test";
import { focusRingMissing, runChecks } from "./checks";

/**
 * The offscreen check itself. A chip row wider than the viewport is fine when
 * it scrolls; the same row clipped by `overflow: hidden`, or by nothing but the
 * viewport, is not.
 */
const row = (overflow: string | null) => `
  <style>
    body { margin: 0; }
    .row { display: flex; gap: 6px; padding: 0 16px; ${overflow ? `overflow-x: ${overflow};` : ""} }
    .chip { flex: none; width: 140px; height: 28px; border: 1px solid #888; }
  </style>
  <div class="row">${Array.from({ length: 10 }, (_, i) => `<span class="chip">chip ${i}</span>`).join("")}</div>`;

test.use({ viewport: { width: 390, height: 600 } });

test("checks/offscreen: scrolling chip row is not offscreen", async ({ page }) => {
  await page.setContent(row("auto"));
  expect((await runChecks(page, true, [])).offscreenRight.count).toBe(0);
});

test("checks/offscreen: chips scrolled past a scroller that stops short of the edge", async ({ page }) => {
  const chips = Array.from({ length: 10 }, (_, i) => `<span class="chip">chip ${i}</span>`).join("");
  await page.setContent(`
    <style>
      body { margin: 0; }
      .bar { display: grid; grid-template-columns: minmax(0, 1fr) auto; }
      .row { display: flex; gap: 6px; overflow-x: auto; }
      .chip { flex: none; width: 140px; height: 28px; }
      button { width: 120px; }
    </style>
    <div class="bar"><div class="row">${chips}</div><button>Follow</button></div>`);
  expect((await runChecks(page, true, [])).offscreenRight.count).toBe(0);
});

test("checks/offscreen: overflow hidden still counts", async ({ page }) => {
  await page.setContent(row("hidden"));
  expect((await runChecks(page, true, [])).offscreenRight.count).toBeGreaterThan(0);
});

test("checks/offscreen: no clipping ancestor still counts", async ({ page }) => {
  await page.setContent(row(null));
  expect((await runChecks(page, true, [])).offscreenRight.count).toBeGreaterThan(0);
});

test("checks/offscreen: a fixed panel's own scroll does not exempt what it cuts off", async ({ page }) => {
  // A peek: fixed, overflow-y auto (so x computes auto too), a head row wider than the pane.
  await page.setContent(`
    <style>body { margin: 0; } .peek { position: fixed; top: 0; right: -40px; bottom: 0; width: 300px; overflow-y: auto; }
      .head { display: flex; gap: 8px; white-space: nowrap; } .head > * { flex: none; }</style>
    <aside class="peek" aria-label="peek"><div class="head"><code>c7446dca…49a7b11</code><span>running</span><a href="#">Open →</a><button>✕</button></div></aside>`);
  expect((await runChecks(page, true, [])).offscreenRight.count).toBeGreaterThan(0);
});

test("checks/offscreen: a page-filling scroller does not exempt a row past the edge", async ({ page }) => {
  await page.setContent(`
    <style>body { margin: 0; } main { height: 100vh; overflow: auto; } .wide { display: flex; } .wide > span { flex: none; width: 160px; }</style>
    <main><div class="wide"><span>a</span><span>b</span><span>c</span><span>d</span></div></main>`);
  expect((await runChecks(page, true, [])).offscreenRight.count).toBeGreaterThan(0);
});

/** data-allow-ellipsis exempts the element carrying it, and nothing else. */
test("checks/ellipsis: data-allow-ellipsis skips a deliberate cut, a plain one still counts", async ({ page }) => {
  const cut = (attr: string) => `<span ${attr} style="display:block;width:120px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">.claude/worktrees/c7446dca30d840a8a69977c6649a7b11/docs/superpowers/plans/x.md</span>`;
  await page.setContent(`<div data-allow-ellipsis>${cut("")}</div>${cut("data-allow-ellipsis")}`);
  expect((await runChecks(page, false, [])).clippedEllipsis.count).toBe(1);
});

/** The contrast check: muted text still has to clear 4.5:1; only text nobody
 *  has to read (disabled, placeholder, aria-hidden, faded decoration) is exempt. */
const ground = (body: string) => `<style>body { margin: 0; background: #0f1019; font: 13px sans-serif; }</style>${body}`;

test("checks/contrast: #9397ab on #0f1019 passes, #5a5d70 fails", async ({ page }) => {
  await page.setContent(ground(`<p style="color:#9397ab">readable muted text</p>`));
  expect((await runChecks(page, false, [])).lowContrast.count).toBe(0);
  await page.setContent(ground(`<p style="color:#5a5d70">too faint muted text</p>`));
  expect((await runChecks(page, false, [])).lowContrast.count).toBe(1);
});

/** Gradients are grounds too (Kraft-aqrs9): the worst colour stop decides. */
test("checks/contrast: light text on a dark→light gradient fails, on dark→darker passes", async ({ page }) => {
  const over = (grad: string) => ground(`<div style="background:${grad};padding:40px"><p style="color:#e9e9ed">text over a gradient</p></div>`);
  await page.setContent(over("linear-gradient(#0f1019, #f4f4f8)"));
  expect((await runChecks(page, false, [])).lowContrast.count).toBe(1);
  await page.setContent(over("linear-gradient(#0f1019, #232532)"));
  expect((await runChecks(page, false, [])).lowContrast.count).toBe(0);
  // A 1px divider drawn as a gradient is not the ground under the text.
  await page.setContent(over("linear-gradient(#f4f4f8, #f4f4f8) no-repeat bottom / 100% 1px, #0f1019"));
  expect((await runChecks(page, false, [])).lowContrast.count).toBe(0);
});

/** The focus-ring check (Kraft-s400i): it has to fire on a real missing ring. */
const buttons = (ring: boolean) => `
  <style>:focus { outline: none; } ${ring ? "button:focus-visible { outline: 2px solid #968ae0; outline-offset: 2px; }" : ""}</style>
  <button id="a">first</button> <button id="b">second</button>`;

test("checks/focus-ring: a Tab-focused outline:none button counts 1", async ({ page }) => {
  await page.setContent(buttons(false));
  await page.keyboard.press("Tab");
  expect(Number(await focusRingMissing(page, false))).toBe(1);
  await page.setContent(buttons(true));
  await page.keyboard.press("Tab");
  expect(Number(await focusRingMissing(page, false))).toBe(0);
});

test("checks/focus-ring: a mouse-focused ringless button counts only on keyboard steps", async ({ page }) => {
  await page.setContent(buttons(true));
  await page.click("#b");
  expect(Number(await focusRingMissing(page, false))).toBe(0);
  expect(Number(await focusRingMissing(page, true))).toBe(1);
});

test("checks/contrast: decoration is exempt, faded controls are not", async ({ page }) => {
  await page.setContent(ground(`
    <p style="color:#5a5d70" aria-hidden="true">hidden from AT</p>
    <p style="color:#5a5d70" class="placeholder">placeholder</p>
    <p style="color:#9397ab; opacity:.3">watermark</p>`));
  expect((await runChecks(page, false, [])).lowContrast.count).toBe(0);
  await page.setContent(ground(`<button style="background:none;border:0;color:#9397ab;opacity:.3">faded action</button>`));
  expect((await runChecks(page, false, [])).lowContrast.count).toBe(1);
});
