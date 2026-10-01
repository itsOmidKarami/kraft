import { createItem, expect, test } from "./fixtures";
import { scaledTimeout } from "../e2e-timing";

// The phone contract at 390x844, against a real server: nothing scrolls
// sideways, every tap target is at least 44px both ways, a text input is at
// least 16px (under it mobile Safari zooms on focus and never zooms back),
// and a real diff fits. jsdom has no viewport, so this is the only place the
// phone's media queries are real. Writes screenshots to frontend/e2e-shots/.

type Fit = { overflowX: number; small: string[] };

async function fit(page: import("@playwright/test").Page): Promise<Fit> {
  return page.evaluate(() => {
    const small: string[] = [];
    for (const e of document.querySelectorAll("a[href], button, input, textarea, select, [role=button], [role=tab], [role=switch]")) {
      const r = e.getBoundingClientRect();
      if (!r.width || !r.height || getComputedStyle(e).visibility === "hidden") continue;
      if (r.width < 44 || r.height < 44) small.push(`${(e.getAttribute("aria-label") || e.textContent || e.tagName).trim().slice(0, 40)} ${Math.round(r.width)}x${Math.round(r.height)}`);
    }
    return { overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth, small };
  });
}

test("the board, an item at a gate, its reject composer and a real diff fit a phone", async ({ page }) => {
  const title = `phone gate ${Date.now()}`;
  const id = await createItem(page, title, "default");
  await page.setViewportSize({ width: 390, height: 844 });

  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Needs you" }).getByRole("button", { name: new RegExp(`^${title} `) })).toBeVisible({ timeout: scaledTimeout(100_000) });
  expect(await fit(page)).toEqual({ overflowX: 0, small: [] });
  await page.screenshot({ path: "e2e-shots/phone-01-board.png", fullPage: true });

  await page.goto(`/work-items/${id}`);
  await expect(page.getByRole("region", { name: "Waiting for your approval" })).toBeVisible();
  expect(await fit(page)).toEqual({ overflowX: 0, small: [] });
  await page.screenshot({ path: "e2e-shots/phone-02-item-gate.png", fullPage: true });

  await page.getByRole("button", { name: "Reject…" }).click();
  const note = page.getByRole("textbox", { name: "Your note" });
  await expect(note).toBeVisible();
  expect(await note.evaluate((e) => parseFloat(getComputedStyle(e).fontSize))).toBeGreaterThanOrEqual(16);
  expect(await fit(page)).toEqual({ overflowX: 0, small: [] });
  await page.screenshot({ path: "e2e-shots/phone-03-reject.png", fullPage: true });
  await page.getByRole("button", { name: "Cancel" }).click();

  await page.getByRole("button", { name: /Review changes/ }).click();
  await page.getByRole("button", { name: /^calc\.py / }).click();
  await expect(page.getByRole("region", { name: "Diff of calc.py" })).toContainText("return a + b");
  expect(await fit(page)).toEqual({ overflowX: 0, small: [] });
  await page.screenshot({ path: "e2e-shots/phone-04-diff.png", fullPage: true });
});
