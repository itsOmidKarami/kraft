import { expect, test } from "./fixtures";

// Against the index e2e/serve.py seeds with two .engineering/ documents.

async function openSearch(page: import("@playwright/test").Page) {
  await page.goto("/");
  // The ⌘K listener is live once the sidebar's Search button is.
  await expect(page.getByRole("button", { name: "Search" })).toBeVisible();
  await page.keyboard.press("Control+k");
  const overlay = page.getByRole("dialog", { name: "Search" });
  await expect(overlay).toBeVisible();
  return overlay;
}

test("Ctrl-K finds an indexed document, and a hit opens it", async ({ page }) => {
  const overlay = await openSearch(page);
  await overlay.getByRole("combobox", { name: "Search" }).fill("reconnect backoff");
  const hit = overlay.getByRole("option", { name: /^WS transport design/ });
  await expect(hit.locator(".ng-search-tag")).toHaveText("specs");
  await expect(hit).toContainText("reconnect backoff schedule caps");

  // A document no work item links to opens in the document dialog.
  await hit.click();
  const doc = page.getByRole("dialog", { name: "WS transport design" });
  await expect(doc).toContainText(".engineering/specs/ws.md");
  await expect(doc).toContainText("reconnect backoff schedule caps");
});

test("the kind filter narrows documents", async ({ page }) => {
  const overlay = await openSearch(page);
  await overlay.getByRole("combobox", { name: "Search" }).fill("board");
  const plan = overlay.getByRole("option", { name: /^UI plan/ });
  await expect(plan).toBeVisible();
  await expect(plan.locator(".ng-search-tag")).toHaveText("plans");

  // The guarantee is that nothing outside the kind comes back, not that
  // nothing does: hybrid search can surface a near spec for this query.
  await overlay.getByRole("button", { name: "Filters" }).click();
  await overlay.getByRole("textbox", { name: "Kind" }).fill("specs");
  await expect(plan).toBeHidden();
  await overlay.getByRole("textbox", { name: "Kind" }).fill("plans");
  await expect(plan).toBeVisible();
});
