// Intake from existing documents, against the real index: the composer's
// type-to-search picker, the chain it trims, and the item that results.
// Each step waits on the thing it screenshots, not on a sleep.
import { at, createdId, expect, openComposer, test } from "./fixtures";

test("intake from an existing spec and plan, end to end", async ({ page }) => {
  const composer = await openComposer(page, `add auth from an existing plan ${Date.now()}`, "default");
  await expect(composer.getByText("19 of 19 nodes run · 5 gates")).toBeVisible();

  for (const [kind, query, path] of [
    ["spec", "transport", ".engineering/specs/ws.md"],
    ["plan", "board", ".engineering/plans/ui.md"],
  ] as const) {
    await composer.getByRole("button", { name: `+ ${kind}` }).click();
    const picker = page.getByRole("dialog", { name: `Attach a ${kind}` });
    await picker.getByRole("textbox").pressSequentially(query);
    await picker.getByRole("button", { name: path }).click();
    await expect(composer.getByText(`${kind} · ${path}`)).toBeVisible();
  }
  // The nodes whose gates the documents stand in for leave the chain.
  await expect(composer.getByText("15 of 19 nodes run · 3 gates")).toBeVisible();
  await page.screenshot({ path: "e2e-shots/v2-attach-composer.png", fullPage: true });

  await composer.getByRole("button", { name: "Create paused" }).click();
  const id = await createdId(page);
  await page.goto(at(`/work-items/${id}`));
  const chain = page.getByRole("group", { name: "default" });
  await expect(chain.getByRole("button", { name: /^implementation, node, / })).toBeVisible();
  await expect(chain.getByRole("button", { name: /^(spec|plan|spec_approval|plan_approval), / })).toHaveCount(0);
  const pane = page.getByRole("complementary", { name: "default pane" });
  await expect(pane).toContainText("15 nodes");
  await expect(pane).toContainText("work item attachments");
  // Reading the attached documents from the item page is Kraft-9d8b2.29.
  await page.screenshot({ path: "e2e-shots/v2-attach-item.png", fullPage: true });
});
