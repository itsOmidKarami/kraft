import { expect, test } from "./fixtures";

// Assumes an orchestrator is already running at baseURL with:
//   - KRAFT_FRONTEND_DIST pointed at ../dist
//   - the fake agent wired (KRAFT_FAKE_AGENT=fix)
//   - KRAFT_E2E_REPO set to a git repo path with a failing test
// See e2e/README.md and e2e/serve.py for the setup.
const REPO = process.env.KRAFT_E2E_REPO!;

test("create a work item and watch it complete", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /new work item/i }).click();

  // Scope to the modal: the Board toolbar also has an aria-label="repo" control.
  const modal = page.getByRole("dialog", { name: "New work item" });
  await modal.getByLabel("repo").fill(REPO);
  await modal.getByLabel("title").fill("make the failing test pass");
  await modal.getByRole("button", { name: /create/i }).click();

  // Navigated to the detail route.
  await expect(page.locator(".detail h2")).toHaveText("make the failing test pass");

  // WorkItemDetail has no work-item status badge; the terminal signal on this
  // route is the work_item_completed row in the EventTimeline.
  await expect(page.locator('[data-type="work_item_completed"]')).toBeVisible({
    timeout: 100_000,
  });

  // Back on the Board, the card's status badge reads "completed".
  await page.goto("/");
  await expect(page.locator('.board-card [data-status="completed"]')).toBeVisible();
});
