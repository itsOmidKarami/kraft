import { expect, test } from "./fixtures";

// Assumes an orchestrator is already running at baseURL with:
//   - KRAFT_FRONTEND_DIST pointed at ../dist
//   - the fake agent wired (KRAFT_FAKE_AGENT=fix)
//   - KRAFT_E2E_REPO set to a git repo path with a failing test
// See e2e/README.md and e2e/serve.py for the setup.
const REPO = process.env.KRAFT_E2E_REPO!;

test("create a work item and watch it complete", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /new work item/i }).click();

  const modal = page.getByRole("dialog", { name: "New work item" });
  await modal.getByLabel("repo").fill(REPO);
  await modal.getByLabel("title").fill("make the failing test pass");
  // Explicit: this spec watches a chain run to completion unattended, which
  // only the gateless quick-task chain does. `default` is what the modal now
  // pre-selects, and it stops at spec_approval.
  //
  // By label text, not `getByRole("radio")`: the input itself is visually
  // hidden behind the segmented control, so a real browser refuses to click it
  // even though jsdom is happy to. Same shape as lifecycle.spec.ts's helper.
  await modal
    .getByRole("radiogroup", { name: "template" })
    .getByText("quick-task", { exact: true })
    .click();
  await modal.getByRole("button", { name: /create/i }).click();

  // Navigated to the detail route.
  await expect(page.locator(".detail h2")).toHaveText("make the failing test pass");
  const wid = new URL(page.url()).pathname.split("/").pop()!;

  // WorkItemDetail has no work-item status badge; the terminal signal on this
  // route is the work_item_completed row in the timeline, which is behind its own
  // tab on the redesigned detail screen.
  await page.getByRole("tab", { name: /Timeline/ }).click();
  await expect(page.locator('[data-type="work_item_completed"]')).toBeVisible({
    timeout: 100_000,
  });

  // 4B/4B-UI: the agent's session summary is ingested and linked to this item,
  // and the panel renders it. Proof of the whole path in a browser. Documents
  // live behind a tab on the redesigned detail screen.
  await page.getByRole("tab", { name: /Documents/ }).click();
  const docs = page.locator(".linked-docs");
  await expect(docs.getByText(/\.engineering\/sessions\//)).toBeVisible({ timeout: 30_000 });
  await docs.getByRole("button").first().click();
  const viewer = page.getByRole("dialog", { name: "document" });
  await expect(viewer).toBeVisible();
  // the modal's meta line carries the document's kind, falling back to its source
  await expect(viewer.getByText("sessions", { exact: true })).toBeVisible();
  await viewer.getByRole("button", { name: /close/i }).click();

  // Back on the Board, this item has moved into the Done group — the redesigned
  // board conveys status by grouping, not by a per-row badge. Scoped by id: the
  // board accumulates rows across runs against a shared server, so a bare
  // ".board-row" locator is a strict-mode violation waiting to happen.
  await page.goto("/");
  const done = page.locator("section", {
    has: page.locator('.group-label:text-is("Done")'),
  });
  await expect(done.locator(`.board-row a[href="/work-items/${wid}"]`)).toBeVisible();
});
