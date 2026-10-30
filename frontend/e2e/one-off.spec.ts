import { connectRepo, expect, REPO, status, test } from "./fixtures";
import { scaledTimeout } from "../e2e-timing";

// A chain written for one item only: designed on the new-item page, read and
// approved before the item is filed. Its Start review is walked below.

test("design a one-off chain on the new-item page, approve it and create the item", async ({ page }) => {
  await connectRepo(page, REPO);
  await page.goto(`/work-items/new?${new URLSearchParams({ repo: REPO, chain: "quick-task", title: "ui one-off design" })}`);
  await page.getByRole("button", { name: /^quick-task/ }).click();
  await page.getByRole("menuitem", { name: "Customize for this item" }).click();
  await expect(page.getByRole("button", { name: /^one-off · extends quick-task/ })).toBeVisible();

  await page.locator(".seam").first().click();
  await page.getByRole("menuitem", { name: /Custom agent node/ }).click();
  await page.getByRole("textbox", { name: "Node id" }).fill("load_test");
  await page.getByRole("textbox", { name: "Prompt" }).fill("run k6 against staging");
  await page.getByRole("button", { name: /Add node/ }).click();
  await expect(page.locator(".graph-node.one-off-custom")).toHaveCount(1);

  await page.getByRole("button", { name: "Create paused" }).click();
  const review = page.getByRole("dialog", { name: /Approve this chain/ });
  await expect(review.getByText("run k6 against staging")).toBeVisible();
  const sent = page.waitForRequest((r) => r.method() === "POST" && new URL(r.url()).pathname === "/api/work-items");
  await review.getByRole("button", { name: "Approve and create" }).click();
  const body = (await sent).postDataJSON();
  expect(body.approve_one_off).toMatch(/^[0-9a-f]{64}$/);
  expect(body.one_off_chain).toContain("load_test");
  expect(body.chain_template).toBeUndefined();

  await expect(page).toHaveURL(/\/\?sel=/);
  const id = new URL(page.url()).searchParams.get("sel")!;
  const item = await (await page.request.get(`/api/work-items/${id}`)).json();
  expect(item.one_off).toMatchObject({ extends: "quick-task", needs_review: false });
});

test("Start reads a one-off chain's custom content first, and Approve and start runs it", async ({ page }) => {
  await connectRepo(page, REPO);
  const text = "id: one-off\nextends: quick-task\nadd:\n  - after: implementation\n    node:\n      id: load_test\n      kind: exec\n      tasks:\n        - id: run\n          kind: agent\n          harness: claude\n          prompt: run k6 against staging\n";
  const made = await page.request.post("/api/work-items", { data: { title: "ui one-off start", repo: REPO, one_off_chain: text, autostart: false } });
  expect(made.status()).toBe(201);
  const id = (await made.json()).id;
  // Nobody approved it: the server will not start it, and the page says so by reading it first.
  expect((await page.request.post(`/api/work-items/${id}/resume`, { data: {} })).status()).toBe(409);

  await page.goto(`/work-items/${id}`);
  await page.getByRole("banner").getByRole("button", { name: "Start" }).click();
  const review = page.getByRole("dialog", { name: /Approve this chain, then start/ });
  await expect(review.getByText("custom content")).toBeVisible();
  await expect(review.getByText(/run k6 against staging/)).toBeVisible();
  await review.getByRole("button", { name: "Approve and start" }).click();
  await expect(status(page, "DONE")).toBeVisible({ timeout: scaledTimeout(90_000) });
  const item = await (await page.request.get(`/api/work-items/${id}`)).json();
  expect(item.one_off.approval).toMatchObject({ by: "human", door: "web" });
});
