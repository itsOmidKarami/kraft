// Intake from existing artifacts (Kraft-dgh), end to end against the real
// index: the picker, the chain preview's strikethrough, the header's
// `from …` badge and the Documents tab's "attached at intake" tag. Each step
// waits on the thing it screenshots, not on a sleep.
import { connectRepo, expect, REPO, REPO_NAME, test } from "./fixtures";

test("intake from an existing plan, end to end", async ({ page }) => {
  await connectRepo(page, REPO);
  await page.goto("/");
  await page.getByRole("button", { name: /new work item/i }).click();

  const modal = page.getByRole("dialog", { name: "New work item" });
  await modal.getByLabel("repo").selectOption({ label: REPO_NAME });
  await modal.getByLabel("title").fill("add auth from an existing plan");
  await modal.getByRole("radio", { name: /^default\b/ }).click();
  await page.screenshot({ path: "e2e-shots/0-modal.png", fullPage: true });

  // type-to-search picker
  await modal.getByLabel("plan").fill("board");
  const hit = modal.getByRole("option", { name: /UI plan/ });
  await hit.waitFor({ timeout: 20_000 });
  await page.screenshot({ path: "e2e-shots/1-picker.png", fullPage: true });
  await hit.click();

  // free-path fallback, for a document the picker cannot offer
  await modal.getByLabel("spec").fill(".engineering/specs/ws.md");
  await modal.getByLabel("spec").press("Enter");
  // the nodes whose gates the attachments satisfy are struck through
  await expect(modal.locator(".chain-preview s", { hasText: /^spec$/ })).toBeVisible();
  await expect(modal.locator(".chain-preview s", { hasText: /^plan$/ })).toBeVisible();
  await page.screenshot({ path: "e2e-shots/2-preview.png", fullPage: true });

  await modal.getByRole("button", { name: /create and start/i }).click();
  await expect(page.locator(".detail h2")).toHaveText("add auth from an existing plan");
  await expect(page.getByText(/from (spec\+plan|plan\+spec)/)).toBeVisible();
  await page.screenshot({ path: "e2e-shots/3-detail-badge.png", fullPage: true });

  await page.getByRole("tab", { name: /Documents/ }).click();
  // The attachments stand in for the skipped spec and plan nodes, so they are
  // not "this node"'s (implementation) documents.
  await page.locator(".linked-docs").getByRole("button", { name: "all", exact: true }).click();
  await expect(page.getByRole("img", { name: "attached at intake" }).first()).toBeVisible();
  await page.screenshot({ path: "e2e-shots/4-documents.png", fullPage: true });

  await page.getByRole("tab", { name: /Timeline/ }).click();
  await expect(page.getByRole("tab", { name: /Timeline/, selected: true })).toBeVisible();
  await page.screenshot({ path: "e2e-shots/5-timeline.png", fullPage: true });

  await page.goto("/");
  await expect(page.getByTestId("board-card").filter({ hasText: "add auth from an existing plan" })).toBeVisible();
  await page.screenshot({ path: "e2e-shots/6-board.png", fullPage: true });
});
