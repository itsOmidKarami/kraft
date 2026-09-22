import { createItem, expect, test } from "./fixtures";
import { scaledTimeout } from "../e2e-timing";

// Assumes an orchestrator is already running at baseURL with:
//   - KRAFT_FRONTEND_DIST pointed at ../dist
//   - the fake agent wired (KRAFT_FAKE_AGENT=fix)
//   - KRAFT_E2E_REPO set to a git repo path with a failing test
// See e2e/README.md and e2e/serve.py for the setup.

test("create a work item and watch it complete", async ({ page }) => {
  // quick-task: this spec watches a chain run to completion unattended, which
  // only the gateless quick-task chain does (`default` stops at spec_approval).
  const wid = await createItem(page, "make the failing test pass", "quick-task");

  // WorkItemDetail has no work-item status badge; the terminal signal on this
  // route is the work_item_completed row in the timeline, which is behind its own
  // tab on the redesigned detail screen.
  await page.getByRole("tab", { name: /Timeline/ }).click();
  await expect(page.locator('[data-type="work_item_completed"]')).toBeVisible({
    timeout: scaledTimeout(100_000),
  });

  // 4B/4B-UI: the agent's session summary is ingested and linked to this item,
  // and the panel renders it. Proof of the whole path in a browser. Documents
  // live behind a tab on the redesigned detail screen.
  // DocumentModal is gone — documents render in the right pane instead of
  // opening a dialog from a row click.
  await page.getByRole("tab", { name: /Documents/ }).click();
  const docs = page.locator(".linked-docs");
  // W11 · G: Documents opens on "this node" (the last node, verify), where the
  // implementation session's summary sits behind a fold, and a row carries its
  // path in `title` rather than as text. "all" lists every node's documents.
  await docs.getByRole("button", { name: "all", exact: true }).click();
  const session = docs.locator('.doc-row[title*=".engineering/sessions/"]').first();
  await expect(session).toBeVisible({ timeout: scaledTimeout(30_000) });
  await session.click();
  const viewer = page.getByTestId("right-pane-doc");
  await expect(viewer).toBeVisible();
  // W13 · B.4: a session summary's pane header opens with what wrote it -- its
  // hook and run. Under Template Schema V1 a session's `hook_point` is the
  // task's canonical path (`dispatch.py`: `hook_point=task.path`), not a
  // legacy hook name, so this reads `implementation.main.implement` where it
  // used to read `on.implementation.start`. `main` is the step the chain's
  // `tasks:` shorthand normalizes to.
  await expect(viewer.locator(".doc-eyebrow")).toHaveText(
    /^implementation\.main\.implement · (attempt|round|turn) \d+ · /,
  );

  // Back on the Board, this item has moved into the Done group — the redesigned
  // board conveys status by grouping, not by a per-row badge. Scoped by id: the
  // board accumulates rows across runs against a shared server, so a bare
  // ".board-row" locator is a strict-mode violation waiting to happen.
  await page.goto("/");
  const done = page.locator("section", {
    has: page.locator('.group-label:text-is("Done")'),
  });
  // The row is not a link (UI v3 · G1-03): the whole row toggles the peek, so
  // it carries its work item id in `data-id` instead of an href.
  await expect(done.locator(`.board-row[data-id="${wid}"]`)).toBeVisible();
});
