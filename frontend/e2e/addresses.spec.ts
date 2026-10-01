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
