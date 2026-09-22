import { expect, REPO, test } from "./fixtures";
import { scaledTimeout } from "../e2e-timing";

// Manual regression round: drives every operational surface of the SPA against
// a real orchestrator (see e2e/serve.py). Assumes the same fixture server as
// chain.spec.ts.

test("settings: connect a repo", async ({ page }) => {
  // Earlier specs connect REPO through fixtures.connectRepo, so take it off
  // again to drive the real Add repo flow. The server stores the resolved path,
  // so look it up instead of sending REPO as typed.
  const name = REPO.split("/").pop()!;
  const { repos } = await (await page.request.get("/api/repos")).json();
  const stored = repos.find((r: { path: string }) => r.path.endsWith(`/${name}`));
  if (stored) {
    const del = await page.request.delete(`/api/repos?path=${encodeURIComponent(stored.path)}`);
    expect(del.ok()).toBe(true);
  }
  await page.goto("/settings/repos");
  await page.getByRole("button", { name: /connect a repo|add repo|connect/i }).first().click();
  const dlg = page.getByRole("dialog", { name: "Add repo" });
  await dlg.getByRole("textbox").first().fill(REPO);
  // probe is debounced; the submit button unlocks once it lands
  await expect(dlg.getByRole("button", { name: /add|connect/i })).toBeEnabled({ timeout: scaledTimeout(15_000) });
  await dlg.getByRole("button", { name: /add|connect/i }).click();
  await expect(dlg).toBeHidden();
  // the row navigates into the detail pane, not just the list. By data-repo,
  // not text: the name also appears in the probe note and other repos' rows.
  // A suffix match, since the server stores the resolved path.
  await page.locator(`[data-repo$="/${name}"]`).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
});

test("settings: chain templates page loads the chain file and resolves it", async ({ page }) => {
  await page.goto("/settings/chains");
  await expect(page.getByRole("heading", { name: "default" })).toBeVisible({ timeout: scaledTimeout(15_000) });
  await expect(page.getByText("verification", { exact: true })).toBeVisible();
  const yaml = page.getByLabel("chain yaml");
  await expect(yaml).toHaveValue(/id: default/);
  await expect(page.getByText("valid", { exact: true })).toBeVisible();
});

test("settings: library shows a component's chains, and a chain links back to it", async ({ page }) => {
  await page.goto("/settings/library?c=tasks.implementer");
  await expect(page.getByRole("heading", { name: "tasks.implementer" })).toBeVisible({ timeout: scaledTimeout(15_000) });
  await expect(page.getByLabel("library yaml")).toHaveValue(/implementer:/);
  await page.getByRole("link", { name: "quick-task", exact: true }).click();
  await expect(page.getByRole("heading", { name: "quick-task" })).toBeVisible();
  await page.getByRole("link", { name: "tasks.implementer" }).first().click();
  await expect(page.getByRole("heading", { name: "tasks.implementer" })).toBeVisible();
});

test("settings: a library task links to its harness profile, and the profile back to it", async ({ page }) => {
  await page.goto("/settings/library?c=tasks.implementer");
  await expect(page.getByRole("heading", { name: "tasks.implementer" })).toBeVisible({ timeout: scaledTimeout(15_000) });
  await page.getByRole("link", { name: "claude", exact: true }).click();
  await expect(page.getByRole("heading", { name: "claude", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: /^provider / })).toBeVisible();
  await page.getByRole("link", { name: "tasks.implementer" }).click();
  await expect(page.getByRole("heading", { name: "tasks.implementer" })).toBeVisible();
});

test("settings: policy edit saves", async ({ page }) => {
  await page.goto("/settings/policy");
  const attempts = page.getByLabel(/attempts/).first();
  await expect(attempts).toBeVisible({ timeout: scaledTimeout(15_000) });
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
  await page.getByRole("button", { name: "New", exact: true }).click({ timeout: scaledTimeout(15_000) });
  const body = page.getByLabel("steering body");
  await expect(body).toBeVisible();
  // A new file's body effect fires a doomed GET for the not-yet-saved name
  // (404, caught, resets draft/loaded to ""). Under load that GET can still
  // be in flight when Save lands; its stale catch then wipes the just-saved
  // draft back to empty. Let it settle before typing.
  await page.waitForLoadState("networkidle");
  await body.fill("edited by e2e\n");
  await page.getByRole("tab", { name: /diff vs saved/i }).click();
  await expect(page.getByTestId("draft-diff")).toBeVisible();
  await page.getByRole("tab", { name: "edit" }).click();
  await page.getByRole("button", { name: "Save" }).click();
  // Save is async (PUT, then a list reload) -- wait for it to actually land
  // before the hard reload below, or a slow save's request gets cancelled
  // mid-flight and the file never persists.
  await expect(page.getByRole("button", { name: "Save" })).toBeDisabled({
    timeout: scaledTimeout(15_000),
  });
  await page.reload();
  await page.locator(".facet-opt", { hasText: "e2e-steering" }).click();
  await expect(page.getByLabel("steering body")).toHaveValue("edited by e2e\n");
});

test("settings: access shows the bind, and allowed-hosts tags round-trip", async ({ page }) => {
  await page.goto("/settings/access");
  await expect(page.getByText("127.0.0.1").first()).toBeVisible({ timeout: scaledTimeout(15_000) });
  const input = page.getByPlaceholder(/add a host or ip/i);
  await expect(input).toBeVisible({ timeout: scaledTimeout(15_000) });
  await input.fill("e2e.kraft.local");
  await input.press("Enter");
  const chip = page.locator(".chip", { hasText: "e2e.kraft.local" });
  await expect(chip).toBeVisible({ timeout: scaledTimeout(15_000) });
  await page.reload();
  await expect(page.locator(".chip", { hasText: "e2e.kraft.local" })).toBeVisible({
    timeout: scaledTimeout(15_000),
  });
  await page.locator(".chip", { hasText: "e2e.kraft.local" }).click();
  await expect(page.locator(".chip", { hasText: "e2e.kraft.local" })).toBeHidden();
});

test("settings: notify send a test reports a result", async ({ page }) => {
  await page.goto("/settings/notify");
  await page.getByLabel(/webhook url/i).fill("https://ntfy.sh/kraft-e2e-test");
  await page.getByRole("button", { name: "Save" }).first().click();
  const send = page.getByRole("button", { name: /send a test/i });
  await expect(send).toBeEnabled({ timeout: scaledTimeout(15_000) });
  await send.click();
  // The receiver may not exist, but the row must report *something* --
  // status/latency or a clear failure -- never stay on "never sent".
  await expect(page.getByText(/never sent/i)).toBeHidden({ timeout: scaledTimeout(15_000) });
});

test("settings: appearance density and board prefs persist after reload", async ({ page }) => {
  await page.goto("/settings/appearance");
  // The radios' checked state and their onChange handlers are both gated on
  // the theme GET having landed (`theme && preview(...)`). Under load that
  // GET can still be in flight right after goto() resolves (goto only waits
  // for `load`, not for in-page fetches); a click before then is a silent
  // no-op forever, and Save never leaves disabled=true. Let it settle first.
  await page.waitForLoadState("networkidle");
  // By label text, not getByRole("radio"): the input is visually hidden behind
  // the segmented control, so a real browser won't click it.
  await page.getByRole("radiogroup", { name: "density" }).getByText("Comfortable", { exact: true }).click();
  await page.getByRole("radiogroup", { name: "group by" }).getByText("repo", { exact: true }).click();
  await page.getByRole("button", { name: "Save" }).click();
  // Same race as the steering test above: wait for the async save (PUT, then
  // a theme reload) to finish before the hard reload, or a slow save gets
  // cancelled mid-flight and nothing persists.
  await expect(page.getByRole("button", { name: "Save" })).toBeDisabled({
    timeout: scaledTimeout(15_000),
  });
  await page.reload();
  await expect(page.getByRole("radio", { name: "Comfortable" })).toBeChecked();
  await expect(page.getByRole("radio", { name: "repo" })).toBeChecked();
});
