import { connectRepo, expect, test } from "./fixtures";

const REPO = process.env.KRAFT_E2E_REPO!;
const REPO_NAME = REPO.split("/").pop()!;

// The G1 regression: with the peek open at a laptop width the rows used to
// lose 440px and re-flow, wrapping the title to one word per line. The peek
// is an overlay now, so the row's box must not move at all.
test("the peek overlays the list without moving a row", async ({ page }) => {
  await connectRepo(page, REPO);
  await page.goto("/");
  await page.getByRole("button", { name: /new work item/i }).click();
  const modal = page.getByRole("dialog", { name: "New work item" });
  await modal.getByLabel("repo").selectOption({ label: REPO_NAME });
  await modal.getByLabel("title").fill("a work item to check the board's responsive layout");
  await modal
    .getByRole("radiogroup", { name: "template" })
    .getByRole("radio", { name: /^quick-task\b/ })
    .click();
  await modal.getByRole("button", { name: /create and start/i }).click();

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
