import { expect, test } from "./fixtures";

// Assumes an orchestrator is already running at baseURL with KRAFT_INDEX_REPOS
// pointed at the fixture repo seeded with .engineering/ content by e2e/serve.py.
// See e2e/README.md.

/**
 * `page.goto` resolves on load, before React has mounted. The Ctrl-K listener
 * is attached in a layout effect, in the same commit as the header, so a
 * visible Search button means the chord is live (App.test.tsx pins that).
 * Escape handling is vitest's: App.test.tsx.
 */
async function openWithShortcut(page: import("@playwright/test").Page) {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Search" })).toBeVisible();
  await page.keyboard.press("Control+k");
}

// A result row renders its title and its snippet in sibling spans, so bare text
// matching is ambiguous. Target the title span.
const resultTitled = (page: import("@playwright/test").Page, title: string) =>
  page.locator(".search-result-title", { hasText: title });

test("Ctrl-K opens search, a hit opens the document viewer", async ({ page }) => {
  await openWithShortcut(page);
  const overlay = page.getByRole("dialog", { name: "Search" });
  await expect(overlay).toBeVisible();

  await overlay.getByRole("searchbox").fill("reconnect backoff");
  await expect(resultTitled(page, "WS transport design")).toBeVisible();

  await resultTitled(page, "WS transport design").click();
  const viewer = page.getByRole("dialog", { name: "document" });
  await expect(viewer).toBeVisible();
  await expect(viewer.locator(".doc-modal-body")).toContainText("reconnect backoff schedule caps");
  await expect(viewer.getByText(".engineering/specs/ws.md")).toBeVisible();

  await viewer.getByRole("button", { name: /close/i }).click();
  await expect(viewer).toBeHidden();
  await expect(overlay).toBeVisible();
});

test("the advanced kind filter narrows results", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Search" }).click();
  const overlay = page.getByRole("dialog", { name: "Search" });

  await overlay.getByRole("searchbox").fill("board");
  await expect(resultTitled(page, "UI plan")).toBeVisible();

  await overlay.getByRole("button", { name: /advanced/i }).click();

  // The filter's guarantee is that nothing outside the kind comes back — not
  // that there are no results. Search defaults to hybrid, and the vector leg
  // legitimately surfaces a semantically near spec for this query.
  await overlay.getByLabel("kind", { exact: true }).fill("specs");
  await expect(resultTitled(page, "UI plan")).toBeHidden();

  await overlay.getByLabel("kind", { exact: true }).fill("plans");
  await expect(resultTitled(page, "UI plan")).toBeVisible();
});
