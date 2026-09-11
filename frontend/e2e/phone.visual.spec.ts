import { expect, test } from "./fixtures";

/**
 * Sub-project B, spec §4 and §6: everything a notification links to has to work
 * on a phone. jsdom has no viewport, so the unit tests can only assert class
 * boundaries and stylesheet source order — the media queries themselves are
 * only real in a browser. This spec is that browser.
 *
 * Sibling to `attachments.visual.spec.ts`: it writes screenshots to
 * `frontend/e2e-shots/` to be looked at, but unlike that one it also asserts
 * the two things that actually break — horizontal overflow, and an input small
 * enough that mobile Safari zooms the page on focus and never zooms back.
 */

const REPO = process.env.KRAFT_E2E_REPO!;
const SHOTS = "e2e-shots";

// iPhone 14 CSS pixels. Comfortably inside the 640px phone breakpoint, and the
// narrowest device anyone is realistically reading a diff on.
test.use({ viewport: { width: 390, height: 844 } });

/** Does this element's content run off its own right edge? */
const overflowsX = (locator: any) =>
  locator.evaluate((el: HTMLElement) => el.scrollWidth > el.clientWidth + 1);

async function createItem(page: any, title: string, template: string) {
  await page.goto("/");
  await page.getByRole("button", { name: /new work item/i }).click();
  const modal = page.getByRole("dialog", { name: "New work item" });
  await modal.getByLabel("repo").fill(REPO);
  await modal.getByLabel("title").fill(title);
  await modal
    .getByRole("radiogroup", { name: "template" })
    .getByText(template, { exact: true })
    .click();
  await modal.getByRole("button", { name: /create/i }).click();
  await expect(page.locator(".detail h2")).toHaveText(title);
}

test("the board fits a phone", async ({ page }) => {
  await createItem(page, "phone board", "default");
  await page.goto("/");
  await expect(page.locator(".board-row").first()).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: `${SHOTS}/phone-01-board.png`, fullPage: true });
  expect(await overflowsX(page.locator("body"))).toBe(false);
});

test("the gate, its reject textarea and the diff viewer all fit a phone", async ({ page }) => {
  await createItem(page, "phone gate", "default");
  await expect(page.getByText(/approve the spec to continue/i)).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: `${SHOTS}/phone-02-gate.png`, fullPage: true });

  // §4: the reject textarea is the one place a phone user types.
  await page.getByRole("button", { name: /Reject/ }).first().click();
  const note = page.getByLabel("reject note");
  await note.fill("the spec misses the error path");
  await page.screenshot({ path: `${SHOTS}/phone-03-reject.png`, fullPage: true });

  // Under 16px, mobile Safari zooms the viewport on focus and does not zoom
  // back out — which strands the reader mid-rejection. This is the assertion
  // the styles.order unit test cannot make.
  const noteFontPx = await note.evaluate((el: HTMLElement) =>
    parseFloat(getComputedStyle(el).fontSize),
  );
  expect(noteFontPx).toBeGreaterThanOrEqual(16);

  // Touch targets on the actions under it.
  const rejectBtn = page.getByRole("button", { name: /Reject and re-plan/ });
  const box = await rejectBtn.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);

  await page.getByRole("button", { name: /Cancel/ }).click();
});

test("the diff viewer wraps a real diff instead of scrolling sideways", async ({ page }) => {
  // A `quick-task` item runs the fake agent, which edits `calc.py` — so unlike
  // an item sitting at its first gate, this one has an actual diff to render.
  // Asserting against an empty diff viewer is how this check passes while the
  // thing it is meant to catch is still broken.
  await createItem(page, "phone diff", "quick-task");
  await expect(page.getByText(/completed|needs you/i).first()).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: /review changes/i }).click();
  // DiffModal is gone — "Review changes" on a phone opens the m05 node page
  // on the Changes tab instead of a dialog.
  const nodePage = page.getByTestId("phone-node-page");
  await expect(nodePage).toBeVisible();
  // A quick-task's edit is already committed by the time the chain reaches a
  // gate, so it renders under "Landed" — collapsed by default (Kraft-nceo).
  // Open the first file the same way a reader would.
  await nodePage.locator(".diff-files summary").first().click();
  const lines = nodePage.locator(".diff-body > div");
  await expect(lines.first()).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${SHOTS}/phone-04-diff.png`, fullPage: true });

  // The whole point of the phone rules: `white-space: pre` sent a long hunk off
  // the side of the screen with no way back.
  expect(await overflowsX(nodePage)).toBe(false);
  expect(await overflowsX(nodePage.locator(".diff-body"))).toBe(false);
  expect(await overflowsX(page.locator("body"))).toBe(false);

  // The untracked-file list carries session paths far longer than 390px. The
  // page clips them, so nothing "overflows" by scrollWidth — the path is just
  // silently cut in half. Measure the text against its own box instead.
  const clipped = await nodePage.locator(".diff-untracked li").evaluateAll((els) =>
    els.filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => el.textContent),
  );
  expect(clipped).toEqual([]);
  expect(await lines.count()).toBeGreaterThan(0);
});

test("the chain bar's node labels do not collide on a phone", async ({ page }) => {
  // The detail screen renders one label per node. The shipped `default` template
  // has ten, which at 390px leaves ~39px each for names like `human_review` —
  // they overlap into an unreadable smear. The gate screen is one of the two
  // screens a notification links to, so this is on the phone contract.
  await createItem(page, "phone chain", "default");
  await expect(page.getByText(/approve the spec to continue/i)).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: `${SHOTS}/phone-06-chain.png`, fullPage: true });

  // Measure text overflow, not box positions. The labels are `nowrap` flex
  // children, so their *boxes* tile neatly while their *text* paints straight
  // over the neighbour — a box-overlap check passes while the screen is a
  // smear. `scrollWidth > clientWidth` is the one that sees it.
  const overflowing = await page.locator(".chain-bar.lg .chain-label").evaluateAll((els) =>
    els
      .filter((el) => (el as HTMLElement).offsetParent !== null)
      .filter((el) => el.scrollWidth > el.clientWidth + 1)
      .map((el) => el.textContent),
  );
  // Either the labels are hidden at this width (the detail hero already names
  // the current node and counts the rest), or each one fits its own box.
  expect(overflowing).toEqual([]);
});

test("the document viewer hides what a phone cannot do", async ({ page }) => {
  await createItem(page, "phone documents", "default");
  await expect(page.getByText(/approve the spec to continue/i)).toBeVisible({ timeout: 30_000 });
  // DocumentModal is gone — Documents lives behind a tab on the m05 node
  // page, opened by tapping a stage on the phone list (m04).
  await page.locator('[data-testid^="phone-stage-"]').first().click();
  const nodePage = page.getByTestId("phone-node-page");
  await expect(nodePage).toBeVisible();
  await nodePage.getByRole("tab", { name: /documents/i }).click();
  const doc = nodePage.locator(".doc-row").first();
  if ((await doc.count()) === 0) test.skip(true, "no linked document to open");
  await doc.click();
  const viewer = nodePage.getByTestId("right-pane-doc");
  await expect(viewer).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/phone-05-document.png`, fullPage: true });

  // Launching an editor needs a window on one machine or the other; a phone has
  // neither the server's desktop nor a vscode:// handler. Copy path does work.
  await expect(viewer.getByRole("button", { name: /open in/i })).toBeHidden();
  await expect(viewer.getByRole("button", { name: /choose editor/i })).toBeHidden();
  await expect(viewer.getByTitle("Copy path")).toBeVisible();
});

test("the bottom nav reaches every screen and highlights the active tab", async ({ page }) => {
  await createItem(page, "phone shell", "default");
  const nav = page.getByRole("navigation", { name: "primary" });
  await expect(nav).toBeVisible();

  // every tab meets the 44px touch-target floor
  for (const label of ["Board", "Search", "Analytics", "Settings"]) {
    const box = await nav.getByRole("link", { name: label }).boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
  }

  await nav.getByRole("link", { name: "Analytics" }).click();
  await expect(page).toHaveURL(/\/analytics$/);
  await expect(nav.getByRole("link", { name: "Analytics" })).toHaveClass(/active/);

  await nav.getByRole("link", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/settings/);

  await nav.getByRole("link", { name: "Board" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(nav.getByRole("link", { name: "Board" })).toHaveClass(/active/);

  await expect(overflowsX(page.locator("body"))).resolves.toBe(false);
});

test("Search opens as a full screen from the bottom nav, not a modal", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "primary" }).getByRole("link", { name: "Search" }).click();
  await expect(page).toHaveURL(/\/search$/);
  expect(await page.getByRole("dialog", { name: "Search" }).count()).toBe(0);
  await page.getByRole("searchbox").fill("reconnect backoff");
  await expect(page.locator(".search-result-title", { hasText: "WS transport design" })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/phone-07-search.png`, fullPage: true });
});

test("Analytics stacks by-node and by-repo into cards with no horizontal overflow", async ({ page }) => {
  await createItem(page, "phone analytics", "quick-task");
  await expect(page.getByText(/completed|needs you/i).first()).toBeVisible({ timeout: 60_000 });
  await page.goto("/analytics");
  // `.node-row` also matches the `.node-head` header row, which this phone
  // layout hides (asserted below) — `[data-node]` picks a real data row.
  await expect(page.locator(".node-row[data-node]").first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/phone-08-analytics.png`, fullPage: true });
  expect(await overflowsX(page.locator(".analytics-body"))).toBe(false);
  // the head row is redundant once every cell carries its own label
  await expect(page.locator(".node-head")).toBeHidden();
});

test("Templates and Steering show the open-on-desktop notice instead of an editable textarea", async ({ page }) => {
  await page.goto("/settings/templates");
  await expect(page.getByText(/open on desktop to edit/i)).toBeVisible();
  await expect(page.getByLabel("template nodes")).toBeHidden();

  await page.goto("/settings/steering");
  const first = page.locator(".template-list .facet-opt").first();
  if ((await first.count()) > 0) {
    await first.click();
    await expect(page.getByText(/open on desktop to edit/i)).toBeVisible();
    await expect(page.getByLabel("steering body")).toBeHidden();
  }
  await page.screenshot({ path: `${SHOTS}/phone-09-settings.png`, fullPage: true });
});
