import { expect, test, type Page } from "@playwright/test";

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
    const ring = page.locator(".kd-ring");
    await expect(ring).toBeVisible();
    // The ring is around the target: its box holds the target's.
    const [r, t] = [await ring.boundingBox(), await target.boundingBox()];
    expect(r && t && r.x <= t.x && r.y <= t.y && r.x + r.width >= t.x + t.width && r.y + r.height >= t.y + t.height).toBe(true);
    // In the page, by the router: the page was not loaded again, which would start the demo over.
    expect(await page.evaluate(() => performance.getEntriesByType("navigation").length + ":" + performance.timeOrigin)).toBe(before);
    expect(errors).toEqual([]);
  });
}
