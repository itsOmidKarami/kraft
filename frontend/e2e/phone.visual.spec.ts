import { createItem, expect, test } from "./fixtures";
import { scaledTimeout } from "../e2e-timing";

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

const SHOTS = "e2e-shots";

// iPhone 14 CSS pixels. Comfortably inside the 767px phone breakpoint, and the
// narrowest device anyone is realistically reading a diff on.
test.use({ viewport: { width: 390, height: 844 } });

/** Does this element's content run off its own right edge? */
const overflowsX = (locator: any) =>
  locator.evaluate((el: HTMLElement) => el.scrollWidth > el.clientWidth + 1);

test("the board fits a phone", async ({ page }) => {
  await createItem(page, "phone board", "default");
  await page.goto("/");
  await expect(page.locator(".board-row").first()).toBeVisible({ timeout: scaledTimeout(30_000) });
  await page.screenshot({ path: `${SHOTS}/phone-01-board.png`, fullPage: true });
  expect(await overflowsX(page.locator("body"))).toBe(false);
});

test("the gate, its chain bar and its reject textarea all fit a phone", async ({ page }) => {
  await createItem(page, "phone gate", "default");
  await expect(page.getByText(/approve the spec to continue/i)).toBeVisible({ timeout: scaledTimeout(30_000) });
  await page.screenshot({ path: `${SHOTS}/phone-02-gate.png`, fullPage: true });

  // The chain bar renders one label per node. The shipped `default` template
  // has ten, which at 390px leaves ~39px each for names like `human_review` —
  // they overlap into an unreadable smear. The gate screen is one of the two
  // screens a notification links to, so this is on the phone contract.
  //
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

  // §4: the reject textarea is the one place a phone user types.
  await page.getByRole("button", { name: /^Reject$/ }).first().click();
  const note = page.getByLabel("composer message");
  await note.fill("the spec misses the error path");
  await page.screenshot({ path: `${SHOTS}/phone-03-reject.png`, fullPage: true });

  // Under 16px, mobile Safari zooms the viewport on focus and does not zoom
  // back out — which strands the reader mid-rejection. This is the assertion
  // the css.contract unit test cannot make.
  const noteFontPx = await note.evaluate((el: HTMLElement) =>
    parseFloat(getComputedStyle(el).fontSize),
  );
  expect(noteFontPx).toBeGreaterThanOrEqual(16);

  // Touch targets on the actions under it.
  // "Reject and send back" under V1: the gate node authors `reject_to: spec`
  // and the composer's submit label names the target it sends back to.
  const rejectBtn = page.getByRole("button", { name: /Reject and send back/ });
  const box = await rejectBtn.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);

  await page.getByRole("button", { name: /Cancel/ }).click();
});

test("a paused item's needs-you state (m07) and its full-screen steer composer (m08)", async ({
  page,
}) => {
  // KRAFT_SLOW gives the pause something to catch — same recipe as
  // lifecycle.spec.ts's desktop pause/steer/resume test.
  await createItem(page, "phone pause KRAFT_SLOW", "quick-task");
  const pause = page.getByRole("button", { name: /^Pause$/ });
  await expect(pause).toBeEnabled({ timeout: scaledTimeout(30_000) });
  await pause.click();
  await expect(page.getByRole("button", { name: /^Resume$/ })).toBeVisible({ timeout: scaledTimeout(30_000) });
  await page.screenshot({ path: `${SHOTS}/phone-10-needs-you-paused.png`, fullPage: true });
  expect(await overflowsX(page.locator("body"))).toBe(false);

  // m08: the composer is a full-screen page here, not an inline expand.
  await page.getByRole("button", { name: /^Steer$/ }).click();
  const composer = page.getByTestId("phone-composer");
  await expect(composer).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/phone-11-composer.png`, fullPage: true });
  expect(await overflowsX(composer)).toBe(false);
  // Two Cancels: PhoneComposer's own head, and the shared Composer's footer
  // one underneath -- the head one is the full-screen page's own affordance.
  await composer.locator(".phone-composer-head").getByRole("button", { name: /cancel/i }).click();
  await expect(composer).toBeHidden();
});

test("the diff viewer wraps a real diff instead of scrolling sideways", async ({ page }) => {
  // A `quick-task` item runs the fake agent, which edits `calc.py` — so unlike
  // an item sitting at its first gate, this one has an actual diff to render.
  // Asserting against an empty diff viewer is how this check passes while the
  // thing it is meant to catch is still broken.
  await createItem(page, "phone diff", "quick-task");
  await expect(page.getByText(/completed|needs you/i).first()).toBeVisible({ timeout: scaledTimeout(60_000) });
  // W11: Review changes lives under the item card's More actions.
  await page.getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: /review changes/i }).click();
  // DiffModal is gone — "Review changes" on a phone opens the m05 node page
  // on the Changes tab instead of a dialog.
  const nodePage = page.getByTestId("phone-node-page");
  await expect(nodePage).toBeVisible();
  // A quick-task's edit is already committed by the time the chain reaches a
  // gate, so it renders under "Landed". Select the first file the same way
  // a reader would — the tree is flat rows now, not a details/summary list.
  await nodePage.locator('.tree-row[data-kind="file"]').first().click();
  const lines = nodePage.locator(".diff-body > div");
  await expect(lines.first()).toBeVisible({ timeout: scaledTimeout(15_000) });
  await page.screenshot({ path: `${SHOTS}/phone-04-diff.png`, fullPage: true });

  // The whole point of the phone rules: `white-space: pre` sent a long hunk off
  // the side of the screen with no way back.
  expect(await overflowsX(nodePage)).toBe(false);
  expect(await overflowsX(nodePage.locator(".diff-body"))).toBe(false);
  expect(await overflowsX(page.locator("body"))).toBe(false);

  // The untracked-file list carries session paths far longer than 390px. The
  // page clips them, so nothing "overflows" by scrollWidth — the path is just
  // silently cut in half. Measure the text against its own box instead.
  const clipped = await nodePage.locator(".change-row .doc-title").evaluateAll((els) =>
    els.filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => el.textContent),
  );
  expect(clipped).toEqual([]);
  expect(await lines.count()).toBeGreaterThan(0);
});

// Which tab is active is BottomNav.test.tsx's; this is the real phone layout:
// touch targets, the routes, no sideways scroll. No work item: the nav is on
// every page, and a live one only added a race (Kraft-utvg3).
test("the bottom nav reaches every screen at touch size", async ({ page }) => {
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "primary" });
  await expect(nav).toBeVisible();

  // every tab meets the 44px touch-target floor
  for (const label of ["Board", "Search", "Analytics", "Settings"]) {
    const box = await nav.getByRole("link", { name: label }).boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
  }

  await nav.getByRole("link", { name: "Analytics" }).click();
  await expect(page).toHaveURL(/\/analytics$/);

  await nav.getByRole("link", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/settings/);

  await nav.getByRole("link", { name: "Board" }).click();
  await expect(page).toHaveURL(/\/$/);

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
  await expect(page.getByText(/completed|needs you/i).first()).toBeVisible({ timeout: scaledTimeout(60_000) });
  await page.goto("/analytics");
  // `.node-row` also matches the `.node-head` header row, which this phone
  // layout hides (asserted below) — `[data-node]` picks a real data row.
  await expect(page.locator(".node-row[data-node]").first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/phone-08-analytics.png`, fullPage: true });
  expect(await overflowsX(page.locator(".analytics-body"))).toBe(false);
  // the head row is redundant once every cell carries its own label
  await expect(page.locator(".node-head")).toBeHidden();
  // stop-reasons cards (m15): stacked, not the desktop 3-column row
  const stopRows = page.locator(".stop-row");
  if ((await stopRows.count()) > 0) {
    expect(await overflowsX(stopRows.first())).toBe(false);
  }
});

test("m11: Access and Notifications fit a phone in one column, allowed-hosts tags don't overflow", async ({
  page,
}) => {
  await page.goto("/settings/access");
  await expect(page.locator(".settings-section").first()).toBeVisible({ timeout: scaledTimeout(30_000) });
  await page.getByPlaceholder(/add a host or ip/i).fill("m11.kraft.local");
  await page.getByPlaceholder(/add a host or ip/i).press("Enter");
  await expect(page.locator(".chip", { hasText: "m11.kraft.local" })).toBeVisible({
    timeout: scaledTimeout(15_000),
  });
  expect(await overflowsX(page.locator(".host-tags"))).toBe(false);
  await page.screenshot({ path: `${SHOTS}/phone-10-access.png`, fullPage: true });

  await page.goto("/settings/notify");
  await expect(page.locator(".settings-section").first()).toBeVisible({ timeout: scaledTimeout(15_000) });
  expect(await overflowsX(page.locator(".settings-body"))).toBe(false);
  await page.screenshot({ path: `${SHOTS}/phone-11-notify.png`, fullPage: true });
});

test("m15: Analytics, Auto-intake and Appearance stack, the three KPIs one per row", async ({ page }) => {
  await createItem(page, "phone m15", "quick-task");
  await page.goto("/analytics");
  await expect(page.locator(".kpi").first()).toBeVisible({ timeout: scaledTimeout(60_000) });
  const firstKpi = await page.locator(".kpi").first().boundingBox();
  const secondKpi = await page.locator(".kpi").nth(1).boundingBox();
  // W11 · E.5: one tile per row -- the second starts below the first ends.
  expect(firstKpi && secondKpi).toBeTruthy();
  expect(secondKpi!.y).toBeGreaterThanOrEqual(firstKpi!.y + firstKpi!.height);

  await page.goto("/settings/intake");
  await expect(page.locator(".settings-section").first()).toBeVisible({ timeout: scaledTimeout(15_000) });
  expect(await overflowsX(page.locator(".settings-body"))).toBe(false);

  await page.goto("/settings/appearance");
  await expect(page.getByText("Palette", { exact: true })).toBeVisible({ timeout: scaledTimeout(15_000) });
  expect(await overflowsX(page.locator(".settings-body"))).toBe(false);
  await page.screenshot({ path: `${SHOTS}/phone-12-appearance.png`, fullPage: true });
});

test("m16: Login renders on a phone and the error state fits without horizontal scroll", async ({
  page,
}) => {
  // Auth is off for a loopback client (perimeter.py), so no password brings
  // the login screen up here. Answer the API with 401 instead: the app routes
  // to Login on any 401, and /api/login's detail is the error it shows.
  await page.route("**/api/**", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({
        detail: route.request().url().endsWith("/api/login") ? "Wrong password." : "authentication required",
      }),
    }),
  );
  await page.goto("/");
  await expect(page.locator(".login-form")).toBeVisible({ timeout: scaledTimeout(15_000) });
  expect(await overflowsX(page.locator(".login-form"))).toBe(false);
  await page.getByLabel("Password").fill("wrong");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByText("Wrong password.")).toBeVisible({ timeout: scaledTimeout(15_000) });
  expect(await overflowsX(page.locator(".login-form"))).toBe(false);
  await page.screenshot({ path: `${SHOTS}/phone-13-login.png`, fullPage: true });
});

test("Chains and Steering are editable on a phone, not an open-on-desktop notice", async ({ page }) => {
  await page.goto("/settings/chains");
  // W11 · D: one page on a phone, no template drill-down to tap through first.
  await page.getByRole("button", { name: /^verify\b/ }).click();
  await expect(page.getByLabel("fix_loop")).toBeVisible();
  await expect(page.getByText(/open on desktop/i)).toBeHidden();

  await page.goto("/settings/steering");
  const first = page.locator(".settings-index-row, .facet-opt").first();
  if ((await first.count()) > 0) {
    await first.click();
    await expect(page.getByLabel("steering body")).toBeEditable();
    await expect(page.getByText(/open on desktop/i)).toBeHidden();
  }
  await page.screenshot({ path: `${SHOTS}/phone-09-settings.png`, fullPage: true });
});

test("Settings landing shows the m10 drill-down list, not a page body", async ({ page }) => {
  await page.goto("/settings");
  // the desktop sidebar is display:none on a phone but still in the DOM, with
  // the same group labels, so look inside the page body only
  const main = page.getByRole("main");
  await expect(main.getByText("How work runs", { exact: true })).toBeVisible();
  await expect(main.getByText("This instance", { exact: true })).toBeVisible();
  await main.getByText("Repos", { exact: true }).click();
  await expect(page).toHaveURL(/\/settings\/repos$/);
  await expect(main.getByText(/back|settings/i).first()).toBeVisible();
  expect(await overflowsX(page.locator(".settings-body"))).toBe(false);
});
