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

/** Allowlist use (README "data-allow-ellipsis"): the item header's meta line
 *  (W11 A.1) -- a flex line whose parts each cut their own text, whole in title. */
test("checks/ellipsis: the item header's .detail-meta-part cuts are allowed, an unmarked part still counts", async ({ page }) => {
  const line = (attr: string) =>
    `<div class="detail-meta" style="display:flex;width:160px;overflow:hidden;white-space:nowrap;font:12px sans-serif">` +
    `<span class="detail-meta-part" ${attr} title="a-repository-with-an-unreasonably-long-name" style="flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis">a-repository-with-an-unreasonably-long-name</span>` +
    `<span class="detail-meta-part" ${attr} title="default" style="flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis">default</span></div>`;
  await page.setContent(line("data-allow-ellipsis"));
  expect((await runChecks(page, false, [])).clippedEllipsis.count).toBe(0);
  await page.setContent(line(""));
  expect((await runChecks(page, false, [])).clippedEllipsis.count).toBeGreaterThan(0);
});

/** Allowlist use: the item title crumb in the app header (W12.1), one line cut, whole in title. */
test("checks/ellipsis: an .app-header-crumb-current cut is allowed, an unmarked crumb still counts", async ({ page }) => {
  const crumb = (attr: string) =>
    `<span class="app-header-crumb-current" ${attr} title="Design the caching layer for document search: embedding cache keyed by (repo, path, blob_sha)" style="display:block;width:200px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font:13px sans-serif">Design the caching layer for document search: embedding cache keyed by (repo, path, blob_sha)</span>`;
  await page.setContent(crumb("data-allow-ellipsis"));
  expect((await runChecks(page, false, [])).clippedEllipsis.count).toBe(0);
  await page.setContent(crumb(""));
  expect((await runChecks(page, false, [])).clippedEllipsis.count).toBe(1);
});

/** Allowlist use: the document pane's title and path lines (W12.2), each one line cut, whole in title. */
test("checks/ellipsis: the document pane's .doc-modal-name and .doc-path cuts are allowed, unmarked ones still count", async ({ page }) => {
  const path = ".engineering/reviews/2026-09-13-design-the-caching-layer-for-document-search-embedding-cache.md";
  const head = (attr: string) =>
    `<div style="width:220px;font:12px sans-serif">` +
    `<span class="doc-modal-name" ${attr} title="Review: Design the caching layer for document search" style="display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">Review: Design the caching layer for document search</span>` +
    `<span class="doc-path" ${attr} title="${path}" style="display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;direction:rtl;text-align:left"><span dir="ltr">${path}</span></span></div>`;
  await page.setContent(head("data-allow-ellipsis"));
  expect((await runChecks(page, false, [])).clippedEllipsis.count).toBe(0);
  await page.setContent(head(""));
  expect((await runChecks(page, false, [])).clippedEllipsis.count).toBe(2);
});

/** Allowlist use: the board row's title (W11 B.2), one line cut, whole in title. */
test("checks/ellipsis: a .board-row-title cut is allowed, an unmarked title still counts", async ({ page }) => {
  const title = (attr: string) =>
    `<span class="board-row-title" ${attr} title="Design the caching layer for document search" style="display:block;width:140px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font:14px sans-serif">Design the caching layer for document search</span>`;
  await page.setContent(title("data-allow-ellipsis"));
  expect((await runChecks(page, false, [])).clippedEllipsis.count).toBe(0);
  await page.setContent(title(""));
  expect((await runChecks(page, false, [])).clippedEllipsis.count).toBe(1);
});

/** Allowlist use: the board row's meta line (W11 B.2), repo · bead · template · age · reason. */
test("checks/ellipsis: a .board-row-meta cut is allowed, an unmarked meta line still counts", async ({ page }) => {
  const meta = (attr: string) =>
    `<div class="board-row-meta" ${attr} title="kraft · kraft-cb59 · default · 15h ago · approve code_review" style="width:140px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font:11.5px sans-serif"><span>kraft</span> · <code>kraft-cb59</code> · <span>default</span> · <span>15h ago</span> · <span>approve code_review</span></div>`;
  await page.setContent(meta("data-allow-ellipsis"));
  expect((await runChecks(page, false, [])).clippedEllipsis.count).toBe(0);
  await page.setContent(meta(""));
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

test("checks/input<16: 15.5px and up passes (Safari rounds to 16), under that counts", async ({ page }) => {
  await page.setContent(`<input style="font-size:15.6px"><input style="font-size:15.5px"><textarea style="font-size:16px"></textarea>`);
  expect((await runChecks(page, true, [])).smallInputs.count).toBe(0);
  await page.setContent(`<input style="font-size:15.4px"><select style="font-size:14px"><option>a</option></select>`);
  expect((await runChecks(page, true, [])).smallInputs.count).toBe(2);
});
