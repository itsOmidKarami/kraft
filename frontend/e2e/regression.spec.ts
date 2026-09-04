import { expect, test } from "./fixtures";

// Manual regression round: drives every operational surface of the SPA against
// a real orchestrator (see e2e/serve.py). Assumes the same fixture server as
// chain.spec.ts.
const REPO = process.env.KRAFT_E2E_REPO!;



test("settings: connect a repo", async ({ page }) => {
  await page.goto("/settings/repos");
  await page.getByRole("button", { name: /connect a repo|add repo|connect/i }).first().click();
  const dlg = page.getByRole("dialog", { name: "Add repo" });
  await dlg.getByRole("textbox").first().fill(REPO);
  // probe is debounced; the submit button unlocks once it lands
  await expect(dlg.getByRole("button", { name: /add|connect/i })).toBeEnabled({ timeout: 15_000 });
  await dlg.getByRole("button", { name: /add|connect/i }).click();
  await expect(page.getByText(REPO.split("/").pop()!, { exact: false }).first()).toBeVisible();
});

test("settings: chain templates page loads and validates", async ({ page }) => {
  await page.goto("/settings/templates");
  await expect(page.getByLabel("template nodes")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /check|validate/i }).click();
  await expect(page.locator(".save-hint, .settings-note").first()).toBeVisible();
});

test("settings: plugins page lists hooks", async ({ page }) => {
  await page.goto("/settings/plugins");
  await expect(page.getByRole("switch").first()).toBeVisible({ timeout: 15_000 });
});

test("settings: policy edit saves", async ({ page }) => {
  await page.goto("/settings/policy");
  const attempts = page.getByLabel(/attempts/).first();
  await expect(attempts).toBeVisible({ timeout: 15_000 });
  await attempts.fill("4");
  await page.getByRole("button", { name: "Save" }).click();
  await page.reload();
  await expect(page.getByLabel(/attempts/).first()).toHaveValue("4");
});

test("settings: access page shows bind", async ({ page }) => {
  await page.goto("/settings/access");
  await expect(page.getByText("127.0.0.1").first()).toBeVisible({ timeout: 15_000 });
});

test("analytics renders", async ({ page }) => {
  await page.goto("/analytics");
  await expect(page.locator("body")).not.toContainText("Failed to fetch");
  await expect(page.getByText(/work items|throughput|cost/i).first()).toBeVisible({
    timeout: 15_000,
  });
});

test("search overlay finds an indexed document", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Meta+k").catch(() => {});
  const dlg = page.getByRole("dialog", { name: "Search" });
  if (!(await dlg.isVisible().catch(() => false))) {
    await page.keyboard.press("Control+k");
  }
  await expect(dlg).toBeVisible({ timeout: 10_000 });
  await dlg.getByLabel("search").fill("backoff");
  await expect(dlg.locator(".search-result").first()).toBeVisible({ timeout: 15_000 });
});
