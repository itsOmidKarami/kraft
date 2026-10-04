import { expect, test, type Page } from "@playwright/test";
import { SCREENS, type Card, type Tap } from "../../src/ng/phone/screens";
import { app, item } from "./kit";

/**
 * The phone: every screen whose taps need seeded data (a card, a row) is reached by tapping from the board.
 * The list is `src/ng/phone/screens.ts`; the vitest walk (`src/ng/phone/reach.test.tsx`) covers the screens
 * that need no data.
 */

/** A board card is found by the bead of the board fixture it stands for. */
const CARD: Record<Card, string> = { running: "kraft-91bc", capped: "kraft-7d21", gate: "kraft-cb59", escalated: "kraft-2c77" };

async function tap(p: Page, t: Tap) {
  if ("role" in t) return void (await p.getByRole(t.role, { name: new RegExp(t.name, "i") }).first().click());
  const loc = t.card ? p.locator(t.css).filter({ hasText: CARD[t.card] }) : p.locator(t.css);
  await loc.first().click();
}

for (const s of SCREENS.filter((s) => s.data)) {
  test(`phone: ${s.id} is reached by tapping from the board`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await app(page, "/", { noShell: true, mock: { harnesses: "floor", ngLibrary: "draft", apply: "none" } });
    for (const t of s.taps) await tap(page, t);
    const want = new RegExp(`^${s.route.replace(/:[^/]+/g, "[^/]+")}/?$`);
    await expect.poll(() => new URL(page.url()).pathname, { timeout: 6000 }).toMatch(want);
    await expect(page.locator("main h1").first()).toBeVisible();
    if (s.heading) await expect(page.getByRole("heading", { level: 1, name: s.heading })).toBeVisible();
  });
}

test("phone: resizing to a desktop window swaps the app and keeps the node open, and back", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await app(page, item("running", "/nodes/verification"), { noShell: true });
  const at = () => new URL(page.url()).pathname;
  await expect(page.locator(".ph-strip")).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.locator(".ng-sidebar")).toBeVisible({ timeout: 8000 });
  expect(at()).toMatch(/nodes\/verification$/);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".ph-strip")).toBeVisible({ timeout: 8000 });
  expect(at()).toMatch(/nodes\/verification$/);
});
