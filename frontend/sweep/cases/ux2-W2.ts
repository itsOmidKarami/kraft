import { settingsFor } from "../fixtures";
import { idOf, ng, settle, type Case, type Ctx } from "../cellKit";

/** The search overlay: open it with Ctrl+K, optionally type, and wait for the debounced sections. */
async function ngSearch(c: Ctx, q: string, opts: { docsError?: boolean; noBeads?: boolean } = {}) {
  if (opts.noBeads) await c.page.route("**/api/beads/search*", (r) => r.fulfill({ status: 200, contentType: "application/json", body: '{"query":"","beads":[]}' }));
  if (opts.docsError) await c.page.route("**/api/search*", (r) => r.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"index unavailable"}' }));
  await ng(c, "/settings/policy", {}, { side: "pinned" });
  await c.page.keyboard.press("Control+k");
  const box = c.page.getByRole("combobox", { name: /search/i });
  await box.waitFor({ timeout: 4000 });
  if (q) { await box.fill(q); await c.page.waitForTimeout(700); }
  await settle(c.page, 400);
}
/** The sign-in card; `submit` types a password and presses Enter, against the mock's 401 or 429. The clock's fixed so the countdown reads the same every run. */
async function ngLogin(c: Ctx, opts: { fill?: boolean; submit?: boolean } = {}) {
  await c.page.clock.setFixedTime(new Date("2026-01-01T00:00:00Z"));
  await ng(c, "/", {});
  if (opts.fill || opts.submit) await c.page.getByLabel(/^Password/).fill("hunter2");
  if (opts.submit) { await c.page.keyboard.press("Enter"); await c.page.locator('[role="alert"], [role="timer"]').first().waitFor({ timeout: 4000 }); }
  await settle(c.page, 400);
}
/** The board with no repo connected. A fresh install has the default chain, which the "empty" data lacks, so it's put back. The page clock is installed after load, so the probe rows' reveal steps are ours to advance. */
async function ngFirstRun(c: Ctx, stage: "step1" | "probing" | "probed" | "step2" | "step3") {
  c.S.settings.templates = settingsFor("default", {}).templates;
  await ng(c, "/", {}, { side: "pinned" });
  await c.page.getByRole("heading", { name: "Nothing on the board yet" }).waitFor({ timeout: 4000 });
  if (stage === "step1") return;
  await c.page.clock.install();
  await c.page.getByLabel(/Path to a local git checkout/).fill("/Users/dev/code/acme");
  await c.page.getByRole("button", { name: "+ Add repo" }).click();
  await c.page.getByRole("list", { name: "Probe results" }).waitFor({ timeout: 4000 });
  if (stage === "probing") { await c.page.clock.runFor(500); return settle(c.page, 200); }
  await c.page.clock.runFor(2000);
  if (stage === "probed") return settle(c.page, 200);
  await c.page.getByRole("button", { name: "Add repo", exact: true }).click();
  await c.page.getByRole("button", { name: "Continue" }).click();
  if (stage === "step3") await c.page.getByRole("button", { name: "Continue" }).click();
  await settle(c.page, 400);
}

export const cells: Case[] = [
  // W2 A: an unbuilt page inside the shell.
  { screen: "shell", variant: "placeholder", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => ng(c, "/_nope", {}) },
  // W2 B: the sidebar. Pinned and rail by stored choice; "revealed" is the pointer over the rail.
  { screen: "shell", variant: "pinned", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: (c) => ng(c, "/settings/policy", {}, { side: "pinned" }) },
  { screen: "shell", variant: "rail", data: "default", widths: [1024], shells: [{ mode: "light" }], run: (c) => ng(c, "/settings/policy", {}, { side: "rail" }) },
  { screen: "shell", variant: "revealed", data: "default", widths: [1024], shells: [{ mode: "light" }], run: (c) => ng(c, "/settings/policy", {}, { side: "rail", hover: true }) },
  { screen: "shell", variant: "pinned", data: "default", widths: [1024], run: (c) => ng(c, "/settings/policy", {}, { side: "pinned" }) },
  { screen: "shell", variant: "long-crumb", data: "long", widths: [1024], run: (c) => ng(c, `/work-items/${idOf(c.S, "gate")}`, {}, { side: "rail" }) },
  { screen: "shell", variant: "actions", data: "default", widths: [1280], run: (c) => ng(c, "/_tokens", {}, { side: "pinned" }) },
  { screen: "search", variant: "empty", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => ngSearch(c, "") },
  { screen: "search", variant: "results", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => ngSearch(c, "gate") },
  { screen: "search", variant: "results", data: "default", widths: [1024, 1920], run: (c) => ngSearch(c, "gate") },
  { screen: "search", variant: "no-match", data: "empty", widths: [1280], run: (c) => ngSearch(c, "zzzqx", { noBeads: true }) },
  { screen: "search", variant: "docs-error", data: "default", widths: [1280], run: (c) => ngSearch(c, "gate", { docsError: true }) },
  { screen: "login", variant: "idle", data: "default", widths: [1280], locked: true, shells: [{ mode: "light" }], run: (c) => ngLogin(c) },
  { screen: "login", variant: "idle", data: "default", widths: [1024, 1920], locked: true, run: (c) => ngLogin(c) },
  { screen: "login", variant: "filled", data: "default", widths: [1280], locked: true, run: (c) => ngLogin(c, { fill: true }) },
  { screen: "login", variant: "error", data: "default", widths: [1280], locked: true, login: "wrong", run: (c) => ngLogin(c, { submit: true }) },
  { screen: "login", variant: "locked", data: "default", widths: [1280], locked: true, login: "locked", shells: [{ mode: "light" }], run: (c) => ngLogin(c, { submit: true }) },
  { screen: "firstrun", variant: "step1", data: "empty", widths: [1280], shells: [{ mode: "light" }], run: (c) => ngFirstRun(c, "step1") },
  { screen: "firstrun", variant: "step1", data: "empty", widths: [1024, 1920], run: (c) => ngFirstRun(c, "step1") },
  { screen: "firstrun", variant: "probing", data: "empty", widths: [1280], run: (c) => ngFirstRun(c, "probing") },
  { screen: "firstrun", variant: "probed", data: "empty", widths: [1280], run: (c) => ngFirstRun(c, "probed") },
  { screen: "firstrun", variant: "step2", data: "empty", widths: [1280], shells: [{ mode: "light" }], run: (c) => ngFirstRun(c, "step2") },
  { screen: "firstrun", variant: "step3", data: "empty", widths: [1280], run: (c) => ngFirstRun(c, "step3") },
];
