import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * The dashboard demo, built as the docs site ships it: it boots on its sample data, and each
 * things-to-try Show me (frontend/demo/tries.ts) lands on its page and rings the control it names.
 * A rename or a move in the UI that strands one fails here, not in front of a visitor.
 */

test.use({ viewport: { width: 1440, height: 900 } });

/**
 * The demo's live motion moves the board's items every 8–15 s, which would make a row land on whatever the
 * board then holds. Kept still the way the demo itself keeps it still: `whileShown` pauses motion when the
 * tab is hidden, so the tab reads hidden while the demo boots, and shown from DOMContentLoaded on (after
 * main.ts ran, with no visibilitychange, so motion stays paused and the app sees a shown tab).
 */
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    let booting = true;
    const real = (name: "hidden" | "visibilityState") => Object.getOwnPropertyDescriptor(Document.prototype, name)!.get!;
    const [hidden, state] = [real("hidden"), real("visibilityState")];
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get() { return booting || hidden.call(this); } });
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get() { return booting ? "hidden" : state.call(this); } });
    document.addEventListener("DOMContentLoaded", () => { booting = false; });
  });
});

/** Each entry: the page it lands on, and the control its ring is around, as a person names it. */
const SHOW_ME: [string, RegExp, "button" | "textbox", string | RegExp][] = [
  ["Open a running item", /\/work-items\/[0-9a-f]{32}$/, "button", /, node, running\b/],
  ["Approve a gate", /\/work-items\/[0-9a-f]{32}$/, "button", "Review"],
  ["Review the changes", /\/work-items\/[0-9a-f]{32}\/review$/, "button", "Request changes"],
  ["Steer an agent", /\/work-items\/[0-9a-f]{32}$/, "button", "Pause"],
  ["Shape the process", /\/templates\/chains(\/[^/]+)?$/, "button", "Add a node or gate here"],
];

/** The ring sits around the target: its box holds the target's, once the page and the ring have settled. */
const ringsAround = async (page: Page, target: Locator) => {
  const ring = page.locator(".kd-ring");
  await expect(ring).toBeVisible();
  await expect.poll(async () => {
    const [r, t] = [await ring.boundingBox(), await target.boundingBox()];
    return !!(r && t && r.x <= t.x && r.y <= t.y && r.x + r.width >= t.x + t.width && r.y + r.height >= t.y + t.height);
  }).toBe(true);
};

/** Approve the board's one gate, from its Show me, the way a visitor would. The phone asks to confirm. */
const approveTheGate = async (page: Page, phone = false) => {
  const list = page.locator(".kd-tries");
  if (await list.isHidden()) await page.getByRole("button", { name: /^Things to try/ }).click();
  await page.getByRole("link", { name: "Show me: Approve a gate" }).click();
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await page.getByRole("button", { name: "Approve", exact: true }).first().click();
  if (phone) await page.getByRole("dialog").getByRole("button", { name: "Approve", exact: true }).click();
  // The approve, and the review at the gate it was: two of the five.
  await expect(page.getByRole("button", { name: `Things to try, 2 of ${SHOW_ME.length} done` })).toBeVisible();
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
    const target = page.getByRole(role, { name, exact: true }).first();
    await expect(target).toBeVisible();
    await ringsAround(page, target);
    // Opening the running item is the visit that ticks it, through the shim's watch and the router's address.
    if (entry === "Open a running item") await expect(page.getByRole("button", { name: `Things to try, 1 of ${SHOW_ME.length} done` })).toBeVisible();
    // In the page, by the router: the page was not loaded again, which would start the demo over.
    expect(await page.evaluate(() => performance.getEntriesByType("navigation").length + ":" + performance.timeOrigin)).toBe(before);
    expect(errors).toEqual([]);
  });
}

test("once the only gate is approved, the gate says one is on its way, and Open and Review go to the running item", async ({ page }) => {
  const errors = await boot(page);
  await approveTheGate(page);
  const list = page.getByRole("region", { name: "Things to try" });
  await expect(list.getByRole("link", { name: /^Show me: / })).toHaveText(["Show me", "Show me", "Show me", "Show me"]);
  await expect(list.getByRole("link", { name: "Show me: Approve a gate" })).toBeHidden();
  await expect(page.locator('[data-try="gate"] .kd-try-none')).toHaveText("No item waits at a gate right now; one is on its way.");
  await page.getByRole("link", { name: "Show me: Open a running item" }).click();
  await expect(page).toHaveURL(/\/work-items\/[0-9a-f]{32}$/);
  const running = new URL(page.url()).pathname;
  await ringsAround(page, page.getByRole("button", { name: /, node, running\b/ }).first());
  // No gate waits, so Review goes to that running item's diff, where a comment is what there is to do.
  await page.getByRole("link", { name: "Show me: Review the changes" }).click();
  await expect(page).toHaveURL(new RegExp(`${running}/review$`));
  await ringsAround(page, page.getByRole("button", { name: "Comment on this file", exact: true }).first());
  expect(errors).toEqual([]);
});

test("on a phone, once the gate is approved, its Show me is gone from the list, not just covered", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const errors = await boot(page);
  await approveTheGate(page, true);
  await page.getByRole("button", { name: /^Things to try/ }).click();
  await expect(page.locator('[data-try="gate"] .kd-try-none')).toBeVisible();
  await expect(page.getByRole("link", { name: "Show me: Approve a gate" })).toBeHidden();
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
