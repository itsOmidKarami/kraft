import { createdId, expect, openComposer, test } from "./fixtures";

// The peek lies over the list at every width, so opening it moves and reflows
// nothing: a row's box is the same before and after. The shipped regression
// this replaces was a title rewrapping to a word per line beside a docked pane.
for (const width of [1440, 1100, 900]) {
  test(`the peek opens without reflowing a row at ${width}`, async ({ page }) => {
    // One server serves every run, so the title is this run's own.
    const title = `a row that must not reflow at ${width} ${Date.now()}`;
    // A row click opens the peek only while Appearance says so; another spec
    // on this server may have set Full page, and the board grouped by repo.
    await page.request.put("/api/theme", { data: { board: { group_by: "status", open_in: "peek" } } });
    // Paused, not started: a started item finishes in about two seconds and its
    // row moves from Running to Done, a new element, which a measurement in
    // flight reads as null. The layout is the subject, not the run.
    await (await openComposer(page, title, "quick-task")).getByRole("button", { name: "Create paused" }).click();
    await createdId(page);
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/");
    const row = page.getByRole("button", { name: new RegExp(`^${title} `) });
    await expect(row).toBeVisible();
    const before = (await row.boundingBox())!;

    await row.click();
    const pane = page.getByRole("complementary", { name: / pane$/ });
    await expect(pane).toBeVisible();
    expect(await row.boundingBox()).toEqual(before);
    // Over the row, not beside it: a pane that docked again would start past the row's right edge.
    if (width >= 1024) expect((await pane.boundingBox())!.x).toBeLessThan(before.x + before.width);
  });
}
