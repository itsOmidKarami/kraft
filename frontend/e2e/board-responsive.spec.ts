import { createItem, expect, test } from "./fixtures";

// The G1 regression: with the peek open at a laptop width the rows used to
// lose 440px and re-flow, wrapping the title to one word per line. The peek
// is an overlay now, so the row's box must not move at all.
test("the peek overlays the list without moving a row", async ({ page }) => {
  await createItem(page, "a work item to check the board's responsive layout", "quick-task");

  await page.goto("/");
  await page.setViewportSize({ width: 1100, height: 800 });
  const row = page.getByTestId("board-card").first();
  await expect(row).toBeVisible();
  const before = await row.boundingBox();

  await row.click();
  await expect(page.getByLabel("peek")).toBeVisible();
  const after = await row.boundingBox();

  expect(after).toEqual(before);
});
