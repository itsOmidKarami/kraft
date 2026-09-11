// Throwaway visual check for "intake from existing artifacts" (Kraft-dgh).
// Screenshots the surfaces a unit test cannot see: the picker, the chain
// preview's strikethrough, the badge, and the Documents tab tag.
import { connectRepo, expect, test } from "./fixtures";

const REPO = process.env.KRAFT_E2E_REPO!;
const REPO_NAME = REPO.split("/").pop()!;

test("intake from an existing plan, end to end", async ({ page }) => {
  await connectRepo(page, REPO);
  await page.goto("/");
  await page.getByRole("button", { name: /new work item/i }).click();

  const modal = page.getByRole("dialog", { name: "New work item" });
  await modal.getByRole("button", { name: new RegExp(REPO_NAME, "i") }).click();
  await modal.getByLabel("title").fill("add auth from an existing plan");
  await modal.locator("label.seg-opt", { hasText: /^default\b/ }).click();
  await page.screenshot({ path: "e2e-shots/0-modal.png", fullPage: true });

  // type-to-search picker
  await modal.getByLabel("existing plan").fill("board");
  const hit = modal.getByRole("button", { name: /UI plan/ });
  await hit.waitFor({ timeout: 20_000 });
  await page.screenshot({ path: "e2e-shots/1-picker.png", fullPage: true });
  await hit.click();

  // free-path fallback, for a document the picker cannot offer
  await modal.getByLabel("spec path").fill(".engineering/specs/ws.md");
  await page.waitForTimeout(500);
  await page.screenshot({ path: "e2e-shots/2-preview.png", fullPage: true });

  await modal.getByRole("button", { name: /create and start/i }).click();
  await expect(page.locator(".detail h2")).toHaveText("add auth from an existing plan");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "e2e-shots/3-detail-badge.png", fullPage: true });

  await page.getByRole("tab", { name: /Documents/ }).click();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: "e2e-shots/4-documents.png", fullPage: true });

  await page.getByRole("tab", { name: /Timeline/ }).click();
  await page.waitForTimeout(1000);
  await page.screenshot({ path: "e2e-shots/5-timeline.png", fullPage: true });

  await page.goto("/");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "e2e-shots/6-board.png", fullPage: true });
});
