import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, publish, test } from "./fixtures";

// Every area's write path, the way the new UI writes it: Templates and
// Policy/Auto-intake edit a server-side draft and publish it; Access,
// Notifications and Appearance save on change. Each test reads the result
// back from the API (or after a reload), not from the control it just set.

/** A cell that reads "<label>, <value>. Edit": click, type, Enter. */
async function editCell(page: import("@playwright/test").Page, label: RegExp, value: string) {
  await page.getByRole("button", { name: label }).click();
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.type(value);
  await page.keyboard.press("Enter");
}

test("repos: connect a repo and publish it", async ({ page }) => {
  // A repo of this run's own, so connecting is never a no-op.
  const repo = mkdtempSync(join(tmpdir(), "kraft-e2e-connect-"));
  execFileSync("git", ["init", "-q", "-b", "main", repo]);
  execFileSync("git", ["-C", repo, "-c", "user.name=e2e", "-c", "user.email=e2e@example.invalid", "commit", "-q", "--allow-empty", "-m", "init"]);

  await page.goto("/templates/repos");
  await page.getByRole("button", { name: "Connect repo" }).click();
  const dialog = page.getByRole("dialog", { name: "Connect a repo" });
  await dialog.getByRole("textbox", { name: "Path to a git repository" }).fill(repo);
  await dialog.getByRole("button", { name: "Check" }).click();
  await dialog.getByRole("button", { name: "Connect" }).click();
  await expect(dialog).toBeHidden();
  await publish(page);

  const { repos } = await (await page.request.get("/api/repos")).json();
  expect(repos.map((r: { path: string }) => r.path.split("/").pop())).toContain(repo.split("/").pop());
});

test("chains: a change to a chain publishes to its file", async ({ page }) => {
  const description = `e2e ${Date.now()}`;
  await page.goto("/templates/chains/quick-task");
  const field = page.getByRole("textbox", { name: "description" });
  await field.fill(description);
  await field.press("Tab");
  // The draft is saved per pause; the header leaves "published" once it is.
  await expect(page.getByRole("banner").getByText(/^DRAFT/)).toBeVisible();
  await publish(page);

  const { text } = await (await page.request.get("/api/templates/chains/quick-task")).json();
  expect(text).toContain(description);
});

test("library: a component lists the chains that use it, and links to them", async ({ page }) => {
  await page.goto("/templates/library/tasks.implementer");
  await page.getByRole("link", { name: "quick-task" }).click();
  await expect(page).toHaveURL(/\/templates\/chains\/quick-task\/nodes\/implementation$/);
});

test("harnesses: a harness lists the tasks it runs, and links to them", async ({ page }) => {
  await page.goto("/templates/harnesses");
  await page.getByRole("region", { name: "claude" }).getByRole("link", { name: /^default › implementation\.main\.implement\. Open in Chains$/ }).click();
  await expect(page).toHaveURL(/\/templates\/chains\/default\/nodes\/implementation$/);
});

test("policy: a cap edited and published", async ({ page }) => {
  const usd = 50 + (Date.now() % 40);
  await page.goto("/settings/policy");
  await editCell(page, /^per day, .+\. Edit$/, String(usd));
  await publish(page);

  const policy = await (await page.request.get("/api/policy")).json();
  expect(policy.budget.daily_usd).toBe(usd);
});

test("auto-intake: the interval edited and published", async ({ page }) => {
  await page.goto("/settings/auto-intake");
  // The pane starts on its rail.
  await page.getByRole("button", { name: "Expand bd ready" }).click();
  const before = (await (await page.request.get("/api/intake")).json()).interval_s;
  const minutes = before === 420 ? 6 : 7;
  await editCell(page, /^check every, minutes, \d+ min\. Edit$/, String(minutes));
  await publish(page);

  expect((await (await page.request.get("/api/intake")).json()).interval_s).toBe(minutes * 60);
});

test("access: the port saves on change and survives a reload", async ({ page }) => {
  await page.goto("/settings/access");
  await editCell(page, /^Port \d+, edit$/, "8766");
  await expect(page.getByRole("button", { name: "Port 8766, edit" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Port 8766, edit" })).toBeVisible();
  expect((await (await page.request.get("/api/access")).json()).port).toBe(8766);
  await page.request.put("/api/access", { data: { port: 8765 } });
});

test("notifications: send a test reports a result", async ({ page }) => {
  await page.goto("/settings/notifications");
  await page.getByRole("button", { name: "Webhook", exact: true }).click();
  const pane = page.getByRole("complementary", { name: "Webhook pane" });
  // Nothing listens on port 9: the result is a failure with its reason.
  await pane.getByRole("textbox", { name: "Webhook URL" }).fill("http://127.0.0.1:9/hook");
  await pane.getByRole("button", { name: "Save" }).click();
  await expect.poll(async () => (await (await page.request.get("/api/notify")).json()).url_set).toBe(true);
  const on = pane.getByRole("switch", { name: "Webhook notifications" });
  if ((await on.getAttribute("aria-checked")) !== "true") await on.click();
  await expect(on).toHaveAttribute("aria-checked", "true");
  await pane.getByRole("button", { name: "Send a test" }).click();
  await expect(pane.getByText(/last attempt .*failed: /)).toBeVisible();
});

test("appearance: density and open-in persist after a reload", async ({ page }) => {
  await page.goto("/settings/appearance");
  await page.getByRole("radiogroup", { name: "Density" }).getByRole("radio", { name: "Comfortable" }).check();
  await page.getByRole("radiogroup", { name: "Open items in" }).getByRole("radio", { name: "Full page" }).check();
  await expect.poll(async () => (await (await page.request.get("/api/theme")).json()).board.open_in).toBe("full");
  await page.reload();
  await expect(page.getByRole("radiogroup", { name: "Density" }).getByRole("radio", { name: "Comfortable" })).toBeChecked();
  await expect(page.getByRole("radiogroup", { name: "Open items in" }).getByRole("radio", { name: "Full page" })).toBeChecked();
  // The other specs click rows expecting the peek.
  await page.request.put("/api/theme", { data: { density: "compact", board: { open_in: "peek" } } });
});
