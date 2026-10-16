import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * The dashboard demo, built as the docs site ships it: it boots on its sample data, and each
 * things-to-try Show me (frontend/demo/tries.ts) lands on its page and rings the control it names.
 * A rename or a move in the UI that strands one fails here, not in front of a visitor.
 */

test.use({ viewport: { width: 1440, height: 900 } });

/** Each entry: the page it lands on, and the control its ring is around, as a person names it. */
const SHOW_ME: [string, RegExp, "button" | "textbox", string | RegExp][] = [
  ["Open a running item", /\/work-items\/[0-9a-f]{32}$/, "button", /, node, running\b/],
  ["Approve a gate", /\/work-items\/[0-9a-f]{32}$/, "button", "Review and decide"],
  ["Review the changes", /\/work-items\/[0-9a-f]{32}\/review$/, "button", "Request changes"],
  ["Steer an agent", /\/work-items\/[0-9a-f]{32}$/, "textbox", "Steer"],
  ["Shape the process", /\/templates\/chains(\/[^/]+)?$/, "button", "Add a node or gate here"],
];

/** The ring sits around the target: its box holds the target's. */
const ringsAround = async (page: Page, target: Locator) => {
  const ring = page.locator(".kd-ring");
  await expect(ring).toBeVisible();
  const [r, t] = [await ring.boundingBox(), await target.boundingBox()];
  expect(r && t && r.x <= t.x && r.y <= t.y && r.x + r.width >= t.x + t.width && r.y + r.height >= t.y + t.height).toBe(true);
};

const boot = async (page: Page) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("./");
  await expect(page.getByRole("heading", { name: "Board", level: 1 })).toBeVisible();
  return errors;
};

test("the demo boots on its sample data, with the things to try open on a first visit", async ({ page }) => {
  const errors = await boot(page);
  await expect(page.getByRole("region", { name: "Needs you" })).toBeVisible();
  const list = page.getByRole("region", { name: "Things to try" });
  await expect(list).toBeVisible();
  await expect(list.getByRole("link", { name: /^Show me: / })).toHaveCount(SHOW_ME.length);
  await expect(page.getByRole("button", { name: `Things to try, 0 of ${SHOW_ME.length} done` })).toBeVisible();
  expect(errors).toEqual([]);
});

for (const [entry, url, role, name] of SHOW_ME) {
  test(`Show me: ${entry} lands on its page and rings its target`, async ({ page }) => {
    const errors = await boot(page);
    const before = await page.evaluate(() => performance.getEntriesByType("navigation").length + ":" + performance.timeOrigin);
    await page.getByRole("link", { name: `Show me: ${entry}` }).click();
    await expect(page).toHaveURL(url);
    const target = page.getByRole(role, { name }).first();
    await expect(target).toBeVisible();
    await ringsAround(page, target);
    // Opening the running item is the visit that ticks it, through the shim's watch and the router's address.
    if (entry === "Open a running item") await expect(page.getByRole("button", { name: `Things to try, 1 of ${SHOW_ME.length} done` })).toBeVisible();
    // In the page, by the router: the page was not loaded again, which would start the demo over.
    expect(await page.evaluate(() => performance.getEntriesByType("navigation").length + ":" + performance.timeOrigin)).toBe(before);
    expect(errors).toEqual([]);
  });
}

test("once the only gate is approved, Open and Review still ring a real target, or say none is there", async ({ page }) => {
  const errors = await boot(page);
  await page.getByRole("link", { name: "Show me: Approve a gate" }).click();
  await page.getByRole("button", { name: "Review and decide" }).click();
  await page.getByRole("button", { name: "Approve", exact: true }).first().click();
  await expect(page.getByRole("button", { name: /^Things to try, [2-5] of/ })).toBeVisible();
  await expect(page.locator('[data-try="gate"] .kd-try-none')).toBeVisible();
  for (const [entry, name] of [["Open a running item", /, node, running\b/], ["Review the changes", /^(Request changes|Comment on this file)$/]] as const) {
    const li = page.locator(`[data-try="${entry === "Open a running item" ? "open" : "review"}"]`);
    const go = li.getByRole("link", { name: `Show me: ${entry}` });
    if (await go.isHidden()) {
      await expect(li.locator(".kd-try-none")).toBeVisible();
      continue;
    }
    await go.click();
    await ringsAround(page, page.getByRole("button", { name }).first());
  }
  expect(errors).toEqual([]);
});

for (const [layout, viewport] of [["desktop", { width: 1440, height: 900 }], ["phone", { width: 390, height: 844 }]] as const) {
  test(`on a ${layout}, a click on the badge's empty box reaches the page under it`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await boot(page);
    // Open, so the box is as tall as it gets: the list over the pill, each its own width.
    if (await page.locator(".kd-tries").isHidden()) await page.getByRole("button", { name: /^Things to try/ }).click();
    const through = await page.evaluate(() => {
      const box = document.querySelector(".kd-badge")!;
      const kids = [...box.children].map((c) => c.getBoundingClientRect()).filter((r) => r.width > 1);
      const b = box.getBoundingClientRect();
      for (let y = b.top + 2; y < b.bottom; y += 6)
        for (let x = b.left + 2; x < b.right; x += 6) {
          if (kids.some((r) => x >= r.left - 2 && x <= r.right + 2 && y >= r.top - 2 && y <= r.bottom + 2)) continue;
          return { found: true, badge: !!document.elementFromPoint(x, y)?.closest(".kd-badge") };
        }
      return { found: false, badge: false };
    });
    expect(through).toEqual({ found: true, badge: false });
  });
}
