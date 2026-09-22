import { createItem, expect, pauseRunningAgent, test } from "./fixtures";
import { scaledTimeout } from "../e2e-timing";

// Manual regression round, part 2: the human-in-the-loop controls on the detail
// screen — pause/steer/resume — driven from the real UI.
// Gate approve and reject-and-send-back are walked in planning.spec.ts.

test("pause, steer and resume from the detail screen", async ({ page }) => {
  // The fake agent finishes in milliseconds, so there is nothing to pause
  // unless it is slowed down first. KRAFT_SLOW in the title does that
  // (fixtures/fake-claude.sh) while the task stays an agent task, which keeps
  // `item.steerable` true and the steer box this test needs visible
  // (PausedCard, Kraft-bz9b's last unwired caller).
  const id = await createItem(page, "ui pause steer KRAFT_SLOW", "quick-task");
  await pauseRunningAgent(page, id);
  await expect(page.getByRole("button", { name: /^Steer$/ })).toBeVisible({ timeout: scaledTimeout(30_000) });
  await page.getByRole("button", { name: /^Steer$/ }).click();
  await expect(page.getByRole("button", { name: /Resume with this steer/ })).toBeVisible({
    timeout: scaledTimeout(30_000),
  });
  await page.getByLabel("composer message").fill("try a different approach");
  await page.getByRole("button", { name: /Resume with this steer/ }).click();
  await page.getByRole("tab", { name: /Timeline/ }).click();
  await expect(page.locator('[data-type="work_item_completed"]')).toBeVisible({
    timeout: scaledTimeout(90_000),
  });
});

test("a deep link to a work item loads it, not an empty husk", async ({ page }) => {
  // Opening or refreshing /work-items/<id> is a document navigation, which the
  // server answers with the SPA shell. Cached under that URL, the shell was then
  // served to the SPA's own fetch for the same path: every panel rendered empty
  // and the controls were the ones for a state the item was not in.
  const id = await createItem(page, "deep link reload", "quick-task");
  await expect(page.locator(".detail h2")).toHaveText("deep link reload");
  await page.goto(`/work-items/${id}`); // full page load, not a client-side route
  await page.getByRole("tab", { name: /Timeline/ }).click();
  await expect(page.locator('[data-type="node_started"]').first()).toBeVisible({
    timeout: scaledTimeout(30_000),
  });
  await page.getByRole("tab", { name: /Tasks/ }).click();
  // UI v2 · 05: the Tasks tab is the 340px inspector list now, not
  // `CurrentNodePanel`'s node-grouped `<details>`. Any visible row proves
  // the tab isn't the empty husk this test guards against.
  await expect(page.getByTestId("inspector-tasks").locator(".row:visible").first()).toBeVisible();
});
