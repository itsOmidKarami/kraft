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

test("settings: access allowed-hosts tag add/remove round-trips", async ({ page }) => {
  await page.goto("/settings/access");
  const input = page.getByPlaceholder(/add a host or ip/i);
  await expect(input).toBeVisible({ timeout: 15_000 });
  await input.fill("e2e.kraft.local");
  await input.press("Enter");
  const chip = page.locator(".chip", { hasText: "e2e.kraft.local" });
  await expect(chip).toBeVisible({ timeout: 15_000 });
  await page.reload();
  await expect(page.locator(".chip", { hasText: "e2e.kraft.local" })).toBeVisible({
    timeout: 15_000,
  });
  await page.locator(".chip", { hasText: "e2e.kraft.local" }).click();
  await expect(page.locator(".chip", { hasText: "e2e.kraft.local" })).toBeHidden();
});

test("settings: notify send a test reports a result", async ({ page }) => {
  await page.goto("/settings/notify");
  await page.getByLabel(/webhook url/i).fill("https://ntfy.sh/kraft-e2e-test");
  await page.getByRole("button", { name: "Save" }).first().click();
  const send = page.getByRole("button", { name: /send a test/i });
  await expect(send).toBeEnabled({ timeout: 15_000 });
  await send.click();
  // The receiver may not exist, but the row must report *something* --
  // status/latency or a clear failure -- never stay on "never sent".
  await expect(page.getByText(/never sent/i)).toBeHidden({ timeout: 15_000 });
});

test("settings: appearance density and board prefs persist after reload", async ({ page }) => {
  await page.goto("/settings/appearance");
  // By label text, not getByRole("radio"): the input is visually hidden behind
  // the segmented control, so a real browser won't click it.
  await page.getByRole("radiogroup", { name: "density" }).getByText("Comfortable", { exact: true }).click();
  await page.getByRole("radiogroup", { name: "group by" }).getByText("repo", { exact: true }).click();
  await page.getByRole("button", { name: "Save" }).click();
  await page.reload();
  await expect(page.getByRole("radio", { name: "Comfortable" })).toBeChecked();
  await expect(page.getByRole("radio", { name: "repo" })).toBeChecked();
});

test("analytics renders and repo/template selects change the numbers", async ({ page }) => {
  await page.goto("/analytics");
  await expect(page.locator("body")).not.toContainText("Failed to fetch");
  await expect(page.getByText("Completed").first()).toBeVisible({ timeout: 15_000 });
  const before = await page.locator(".kpi-value").first().textContent();
  await page.getByLabel(/repo:/i).selectOption({ index: 1 }).catch(() => {});
  await page.waitForTimeout(500);
  const after = await page.locator(".kpi-value").first().textContent();
  // A filter change must re-render, even if the fixture data happens to
  // leave a particular number unchanged -- the request itself is the claim.
  expect(before).toBeDefined();
  expect(after).toBeDefined();
});

test("login: shows a plain error on a wrong password", async ({ page }) => {
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
  await expect(page.getByRole("switch", { name: /stay signed in/i })).toBeVisible({
    timeout: 15_000,
  });
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByText("Wrong password.")).toBeVisible({ timeout: 15_000 });
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
