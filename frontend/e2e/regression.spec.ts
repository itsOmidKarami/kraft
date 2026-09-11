import { expect, test } from "./fixtures";

// Manual regression round: drives every operational surface of the SPA against
// a real orchestrator (see e2e/serve.py). Assumes the same fixture server as
// chain.spec.ts.
const REPO = process.env.KRAFT_E2E_REPO!;



test("settings: connect a repo", async ({ page }) => {
  await page.goto("/settings/repos");
  await page.getByRole("button", { name: /connect a repo|add repo|connect/i }).first().click();
  const dlg = page.getByRole("dialog", { name: "Add repo" });
  await dlg.getByRole("textbox").first().fill(REPO);
  // probe is debounced; the submit button unlocks once it lands
  await expect(dlg.getByRole("button", { name: /add|connect/i })).toBeEnabled({ timeout: 15_000 });
  await dlg.getByRole("button", { name: /add|connect/i }).click();
  await expect(dlg).toBeHidden();
  // the row navigates into the detail pane, not just the list. By data-repo,
  // not text: the name also appears in the probe note and other repos' rows.
  // A suffix match, since the server stores the resolved path.
  const name = REPO.split("/").pop()!;
  await page.locator(`[data-repo$="/${name}"]`).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
});

test("settings: chain templates page loads and validates", async ({ page }) => {
  await page.goto("/settings/chains");
  await expect(page.getByText("default").first()).toBeVisible({ timeout: 15_000 });
  // the graph node, not the live YAML pane, which also says "verify"
  await page.getByRole("button", { name: /^verify\b/ }).click();
  await expect(page.getByLabel("fix_loop")).toBeVisible();
});

test("settings: plugins page lists hooks", async ({ page }) => {
  await page.goto("/settings/plugins");
  await expect(page.getByRole("switch").first()).toBeVisible({ timeout: 15_000 });
  await page.getByText("on.test.run").click();
  await expect(page.getByText(/used by/)).toBeVisible();
});

test("settings: policy edit saves", async ({ page }) => {
  await page.goto("/settings/policy");
  const attempts = page.getByLabel(/attempts/).first();
  await expect(attempts).toBeVisible({ timeout: 15_000 });
  await attempts.fill("4");
  const maxConcurrent = page.getByLabel(/max concurrent/i);
  await maxConcurrent.fill("4");
  await page.getByRole("button", { name: "Save" }).click();
  await page.reload();
  await expect(page.getByLabel(/attempts/).first()).toHaveValue("4");
  await expect(page.getByLabel(/max concurrent/i)).toHaveValue("4");
});

test("settings: steering is editable and diffable", async ({ page }) => {
  await page.goto("/settings/steering");
  // the fixture instance ships no steering files, so make one
  page.once("dialog", (d) => d.accept("e2e-steering"));
  await page.getByRole("button", { name: "New", exact: true }).click({ timeout: 15_000 });
  const body = page.getByLabel("steering body");
  await expect(body).toBeVisible();
  await body.fill("edited by e2e\n");
  await page.getByRole("tab", { name: /diff vs saved/i }).click();
  await expect(page.getByTestId("draft-diff")).toBeVisible();
  await page.getByRole("tab", { name: "edit" }).click();
  await page.getByRole("button", { name: "Save" }).click();
  await page.reload();
  await page.locator(".facet-opt", { hasText: "e2e-steering" }).click();
  await expect(page.getByLabel("steering body")).toHaveValue("edited by e2e\n");
});

test("settings: access page shows bind", async ({ page }) => {
  await page.goto("/settings/access");
  await expect(page.getByText("127.0.0.1").first()).toBeVisible({ timeout: 15_000 });
});

test("analytics renders", async ({ page }) => {
  await page.goto("/analytics");
  await expect(page.locator("body")).not.toContainText("Failed to fetch");
  await expect(page.getByText(/work items|throughput|cost/i).first()).toBeVisible({
    timeout: 15_000,
  });
});

test("search overlay finds an indexed document", async ({ page }) => {
  await page.goto("/");
  // The Ctrl-K handler attaches in a useEffect, so it doesn't exist until
  // React has hydrated (search.spec.ts's openWithShortcut documents the same
  // race) -- pressing the chord straight off `goto` is a race this test lost
  // intermittently. Wait for a rendered control first.
  await expect(page.getByRole("button", { name: "Search" })).toBeVisible();
  await page.keyboard.press("Meta+k").catch(() => {});
  const dlg = page.getByRole("dialog", { name: "Search" });
  if (!(await dlg.isVisible().catch(() => false))) {
    await page.keyboard.press("Control+k");
  }
  await expect(dlg).toBeVisible({ timeout: 10_000 });
  await dlg.getByLabel("search").fill("backoff");
  await expect(dlg.locator(".search-result").first()).toBeVisible({ timeout: 15_000 });
});
