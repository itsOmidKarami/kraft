import { expect, test } from "./fixtures";

// Assumes an orchestrator is already running at baseURL with KRAFT_INDEX_REPOS
// pointed at the fixture repo seeded with .engineering/ content by e2e/serve.py.
// See e2e/README.md.

/**
 * The Ctrl-K handler is attached in a `useEffect`, so it does not exist until
 * React has hydrated. `page.goto` resolves on load, well before that — pressing
 * the chord straight away is a race that loses. Wait for a rendered control
 * first; that is the app telling us it is mounted.
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
  await expect(viewer.locator(".doc-body")).toContainText("reconnect backoff schedule caps");
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
  await overlay.getByLabel("kind", { exact: true }).fill("specs");
  await expect(overlay.getByText(/no matches/i)).toBeVisible();

  await overlay.getByLabel("kind", { exact: true }).fill("plans");
  await expect(resultTitled(page, "UI plan")).toBeVisible();
});

test("Escape closes the overlay", async ({ page }) => {
  await openWithShortcut(page);
  await expect(page.getByRole("dialog", { name: "Search" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Search" })).toBeHidden();
});

test("Escape in the document viewer closes only the viewer", async ({ page }) => {
  await openWithShortcut(page);
  const overlay = page.getByRole("dialog", { name: "Search" });
  await overlay.getByRole("searchbox").fill("reconnect backoff");
  await resultTitled(page, "WS transport design").click();

  const viewer = page.getByRole("dialog", { name: "document" });
  await expect(viewer).toBeVisible();
  await page.keyboard.press("Escape");

  // The viewer stops the event so App's window-level handler never sees it.
  await expect(viewer).toBeHidden();
  await expect(overlay).toBeVisible();
});
