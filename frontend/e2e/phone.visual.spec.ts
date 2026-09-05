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
  const dialog = page.getByRole("dialog", { name: "changes" });
  await expect(dialog).toBeVisible();
  const lines = dialog.locator(".diff-body > div");
  await expect(lines.first()).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${SHOTS}/phone-04-diff.png`, fullPage: true });

  // The whole point of the phone rules: `white-space: pre` sent a long hunk off
  // the side of the screen with no way back.
  expect(await overflowsX(dialog)).toBe(false);
  expect(await overflowsX(dialog.locator(".diff-body"))).toBe(false);
  expect(await overflowsX(page.locator("body"))).toBe(false);

  // The untracked-file list carries session paths far longer than 390px. The
  // modal clips them, so nothing "overflows" by scrollWidth — the path is just
  // silently cut in half. Measure the text against its own box instead.
  const clipped = await dialog.locator(".diff-untracked li").evaluateAll((els) =>
    els.filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => el.textContent),
  );
  expect(clipped).toEqual([]);
  expect(await lines.count()).toBeGreaterThan(0);

  // The modal has to fit the viewport, not run past it.
  const dbox = await dialog.boundingBox();
  expect(dbox!.height).toBeLessThanOrEqual(844);
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

test("the document modal hides what a phone cannot do", async ({ page }) => {
  await createItem(page, "phone documents", "default");
  await expect(page.getByText(/approve the spec to continue/i)).toBeVisible({ timeout: 30_000 });
  await page.getByRole("tab", { name: /documents/i }).click();
  const doc = page.locator(".doc-row").first();
  if ((await doc.count()) === 0) test.skip(true, "no linked document to open");
  await doc.click();
  const modal = page.getByRole("dialog", { name: "document" });
  await expect(modal).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/phone-05-document.png`, fullPage: true });

  // Launching an editor needs a window on one machine or the other; a phone has
  // neither the server's desktop nor a vscode:// handler. Copy path does work.
  await expect(modal.getByRole("button", { name: /open in/i })).toBeHidden();
  await expect(modal.getByRole("button", { name: /choose editor/i })).toBeHidden();
  await expect(modal.getByTitle("Copy path")).toBeVisible();
});
