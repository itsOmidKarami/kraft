import { createItem, expect, test } from "./fixtures";

// Addresses from before the cutover still open their page: the new UI's own
// /ng prefix (a bookmark, served the shell by the server, stripped by
// main.tsx), a shipped settings page, and a shipped item hash.
test("old addresses land on their new pages", async ({ page }) => {
  const id = await createItem(page, `old addresses ${Date.now()}`, "quick-task");

  await page.goto(`/ng/work-items/${id}?x=1`);
  await expect(page).toHaveURL(new RegExp(`/work-items/${id}\\?x=1$`));
  await expect(page.getByRole("heading", { level: 1, name: /^old addresses / })).toBeVisible();

  await page.goto("/settings/chains");
  await expect(page).toHaveURL(/\/templates\/chains$/);

  await page.goto(`/work-items/${id}#node=implementation&tab=tasks`);
  await expect(page).toHaveURL(new RegExp(`/work-items/${id}/nodes/implementation$`));
});

// The phone and the desktop share one address space: a phone screen's address,
// widened from 390px to 1024px in the same tab, lands on the nearest desktop
// page, and neither width ever shows Not found (R3-04).
const WIDENED: [from: string, lands: string][] = [
  ["/more", "/"],
  ["/templates/harnesses/claude", "/templates/harnesses?harness=claude"],
  ["/templates/harnesses/profiles/deep", "/templates/harnesses?profile=deep"],
  ["/settings/notifications/webhook", "/settings/notifications"],
  ["/settings/auto-intake/schedules/0", "/settings/auto-intake"],
  ["/archived", "/archived"],
];

test("a phone address still opens a page when the window widens to the desktop", async ({ page }) => {
  const where = () => { const u = new URL(page.url()); return u.pathname + u.search; };
  for (const [from, lands] of WIDENED) {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(from);
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Not found" }), `${from} at 390px`).toHaveCount(0);

    await page.setViewportSize({ width: 1024, height: 800 });
    await expect(page.getByRole("link", { name: "Skip to content" })).toBeAttached();
    await expect.poll(where, { message: `${from} widened` }).toBe(lands);
    await expect(page.getByRole("heading", { name: "Not found" }), `${from} at 1024px`).toHaveCount(0);
  }
});
