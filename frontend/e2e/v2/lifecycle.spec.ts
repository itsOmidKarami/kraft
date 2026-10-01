import { agentRunning, at, createItem, expect, status, test } from "./fixtures";
import { scaledTimeout } from "../../e2e-timing";

// The human-in-the-loop controls on the item page. Gate approve and reject
// are walked in planning.spec.ts.

test("pause, steer and resume from the item page", async ({ page }) => {
  // KRAFT_SLOW keeps the fake agent running (fixtures/fake-claude.sh) long
  // enough to pause it, as an agent task, so the pause stops a session and
  // a steer is taken.
  const id = await createItem(page, "ui pause steer KRAFT_SLOW", "quick-task");
  await page.goto(at(`/work-items/${id}`));
  await agentRunning(page, id);

  await page.getByRole("banner").getByRole("button", { name: "Pause" }).click();
  await page.getByRole("dialog", { name: "Pause this item?" }).getByRole("button", { name: "Pause now" }).click();
  await expect(status(page, "PAUSED")).toBeVisible({ timeout: scaledTimeout(30_000) });
  // The header says PAUSED as the status lands; the walk behind it unwinds a
  // moment later, and only then does the server take a steer.
  await expect
    .poll(async () => (await (await page.request.get(`/api/work-items/${id}`)).json()).steerable, { timeout: scaledTimeout(30_000) })
    .toBe(true);

  const paused = page.getByRole("region", { name: "Paused" });
  await paused.getByRole("textbox", { name: "Steer" }).fill("try a different approach");
  // The server refuses a resume while the paused walk is still unwinding
  // ("a walk is already running", Kraft-9d8b2.28), and the page has no
  // signal for when it has; the steer stays in the box, so send it again.
  await expect(async () => {
    await paused.getByRole("button", { name: "Resume with steer" }).click();
    await expect(status(page, "PAUSED")).toBeHidden({ timeout: 2_000 });
  }).toPass({ timeout: scaledTimeout(30_000) });
  await expect(status(page, "DONE")).toBeVisible({ timeout: scaledTimeout(90_000) });
});

test("a deep link to a work item loads it, not an empty husk", async ({ page }) => {
  // A document navigation to the item's address is answered with the SPA
  // shell; the page's own fetch for the item must still get JSON.
  const id = await createItem(page, "deep link reload", "quick-task");
  await page.goto(at(`/work-items/${id}`));
  await expect(page.getByRole("heading", { level: 1, name: "deep link reload" })).toBeVisible();
  await expect(page.getByRole("group", { name: "quick-task" }).getByRole("button", { name: /^implementation, node, / })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "quick-task pane" })).toContainText("2 nodes");
});
