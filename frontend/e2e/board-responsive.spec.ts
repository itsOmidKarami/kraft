import { createItem, expect, test } from "./fixtures";

// The peek (W6 C.2, R7): docked from 1024, so the list's right edge moves in
// by the pane's width and nothing sits under the pane; overlaid below 1024,
// so the list does not move. Either way a row keeps its height: the shipped
// regression this replaces was a title rewrapping to a word per line.
for (const width of [1440, 1100, 900]) {
  test(`the peek opens without reflowing a row at ${width}`, async ({ page }) => {
    // One server serves every run, so the title is this run's own.
    const title = `a row that must not reflow at ${width} ${Date.now()}`;
    // A row click opens the peek only while Appearance says so; another spec
    // on this server may have set Full page, and the board grouped by repo.
    await page.request.put("/api/theme", { data: { board: { group_by: "status", open_in: "peek" } } });
    await createItem(page, title, "quick-task");
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/");
    const row = page.getByRole("button", { name: new RegExp(`^${title} `) });
    await expect(row).toBeVisible();
    const before = (await row.boundingBox())!;

    await row.click();
    const pane = page.getByRole("complementary", { name: / pane$/ });
    await expect(pane).toBeVisible();
    const after = (await row.boundingBox())!;
    expect(after.height).toBe(before.height);
    if (width >= 1024) expect(after.x + after.width).toBeLessThanOrEqual((await pane.boundingBox())!.x);
    else expect(after).toEqual(before);
  });
}
