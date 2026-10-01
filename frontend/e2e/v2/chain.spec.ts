import { at, createItem, expect, status, test } from "./fixtures";
import { scaledTimeout } from "../../e2e-timing";

// quick-task is the one shipped chain with no gate, so it runs to the end
// unattended (fixtures/fake-claude.sh makes the failing test pass).
test("create a work item and watch it complete", async ({ page }) => {
  const id = await createItem(page, "make the failing test pass", "quick-task");

  await page.goto(at(`/work-items/${id}`));
  await expect(status(page, "DONE")).toBeVisible({ timeout: scaledTimeout(100_000) });

  // The implementation session's summary is indexed and linked to the task
  // that wrote it: node, then step, then task, then its document.
  await page.getByRole("button", { name: "implementation, node, done" }).click();
  await page.getByRole("button", { name: "main implement" }).click();
  await page.getByRole("button", { name: "implement agent" }).click();
  const pane = page.getByRole("complementary", { name: "implement pane" });
  await pane.getByRole("tab", { name: "Log" }).click();
  await expect(pane.getByRole("tabpanel")).toContainText("result: success");
  await pane.getByRole("tab", { name: "Overview" }).click();
  await pane.getByRole("button", { name: / sessions$/ }).click({ timeout: scaledTimeout(30_000) });
  const doc = page.getByRole("dialog");
  await expect(doc).toContainText(".engineering/sessions/");
  await expect(doc).toContainText("implementation › implement › attempt 1");

  // Back on the board, the item sits in the Done group.
  await page.goto(at("/"));
  const done = page.getByRole("region", { name: "Done" });
  await expect(done.getByRole("button", { name: /^make the failing test pass .* completed$/ }).first()).toBeVisible();
});
