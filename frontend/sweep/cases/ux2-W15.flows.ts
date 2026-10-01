import { expect, type Page } from "@playwright/test";
import { settle, type Flow } from "../flowKit";

// W15: a settings area on the mock's draft of it (`mock: { areas }`), sidebar pinned.
const area = (url: string, ready: string) => async (p: Page) => { await p.addInitScript(() => localStorage.setItem("kraft.sidebar.v2", "pinned")); await p.goto(url); await p.locator(ready).first().waitFor({ timeout: 8000 }); await settle(p, 600); };

export const flows: Flow[] = [
  // W15 D: Connect repo: the path, Check, Connect; the new repo is selected and the draft counts one change.
  { name: "repo-connect", widths: [1280], mock: { areas: "default" }, start: area("/templates/repos/platform", ".rp-row"), steps: [
    { name: "open-connect", run: async (p) => { await p.getByRole("button", { name: /Connect repo/ }).click(); await expect(p.getByRole("dialog", { name: "Connect a repo" })).toBeVisible(); } },
    { name: "check", run: async (p) => { await p.getByLabel("Path to a git repository").fill("/Users/me/src/new"); await p.getByRole("button", { name: "Check" }).click(); await expect(p.getByLabel("What was found")).toBeVisible(); } },
    { name: "connect", run: async (p) => { await p.getByRole("dialog", { name: "Connect a repo" }).getByRole("button", { name: "Connect" }).click(); await expect(p.getByText("DRAFT · 1 CHANGE")).toBeVisible(); await expect(p.getByRole("option", { name: /^x,/ })).toHaveAttribute("aria-selected", "true"); }, wait: 300 },
  ] },
  // W15 D: a repo value that widens the instance's safety ceiling is refused inline and nothing is saved.
  { name: "repo-refused", widths: [1280], mock: { areas: "default" }, start: area("/templates/repos/platform", ".rp-row"), steps: [
    { name: "edit", run: async (p) => { await p.getByRole("button", { name: /^allowed tools, not set/ }).click(); await p.getByRole("textbox", { name: "allowed tools" }).fill("Bash"); await p.keyboard.press("Enter"); await expect(p.getByText(/Refused:/)).toBeVisible(); } },
    { name: "nothing-saved", run: async (p) => { await expect(p.getByText("published", { exact: true })).toBeVisible(); await p.keyboard.press("Escape"); await expect(p.getByRole("button", { name: /^allowed tools, not set/ })).toBeVisible(); }, kbd: true },
  ] },
  // W15 E: edit a maximum, Review & publish, publish.
  { name: "policy-edit-publish", widths: [1280], mock: { areas: "default" }, start: area("/settings/policy/limits", ".pol-card"), steps: [
    { name: "edit", run: async (p) => { await p.getByRole("button", { name: /^tasks running maximum, 240 min/ }).click(); await p.getByRole("textbox", { name: "tasks running maximum" }).fill("200"); await p.keyboard.press("Enter"); await expect(p.getByText("DRAFT · 1 CHANGE")).toBeVisible(); } },
    { name: "review", run: async (p) => { await p.getByRole("button", { name: "Review & publish" }).click(); await expect(p.getByRole("heading", { name: "Draft · 1 change" })).toBeVisible(); } },
    { name: "publish", run: async (p) => { await p.getByRole("button", { name: "Publish", exact: true }).click(); await expect(p.locator(".toast", { hasText: "Published policy" })).toBeVisible(); }, wait: 300 },
  ] },
  // W15 E: another draft or save moved policy.yaml; the 409 offers Keep my version, which publishes.
  { name: "policy-stale", widths: [1280], mock: { areas: "stale" }, start: area("/settings/policy/housekeeping", ".pol-card"), steps: [
    { name: "edit-and-publish", run: async (p) => { await p.getByRole("button", { name: /^max active items, 5/ }).click(); await p.getByRole("textbox", { name: "max active items" }).fill("7"); await p.keyboard.press("Enter"); await p.getByRole("button", { name: "Review & publish" }).click(); await p.getByRole("button", { name: "Publish", exact: true }).click(); await expect(p.getByText("Published since this draft began", { exact: true })).toBeVisible(); } },
    { name: "keep-mine", run: async (p) => { await p.getByRole("button", { name: "Keep my version and publish" }).click(); await expect(p.locator(".toast", { hasText: "over the newer version" })).toBeVisible(); }, wait: 300 },
  ] },
  // W15 F: + Add schedule, edit it, and one publish writes both files.
  { name: "intake-schedule", widths: [1280], mock: { areas: "default" }, start: area("/settings/auto-intake", ".ink-card"), steps: [
    { name: "add", run: async (p) => { await p.getByRole("button", { name: /Add schedule/ }).click(); await expect(p.getByRole("complementary", { name: "Scheduled item pane" })).toBeVisible(); } },
    { name: "edit", run: async (p) => { await p.getByRole("button", { name: /^name, Scheduled item/ }).click(); await p.getByRole("textbox", { name: "name" }).fill("Nightly sweep"); await p.keyboard.press("Enter"); await expect(p.getByRole("complementary", { name: "Nightly sweep pane" })).toBeVisible(); } },
    { name: "interval", run: async (p) => { await p.getByRole("button", { name: /^Auto-intake/ }).first().click(); await p.getByRole("button", { name: /^check every, minutes/ }).click(); await p.getByRole("textbox", { name: "check every, minutes" }).fill("2"); await p.keyboard.press("Enter"); } },
    { name: "review", run: async (p) => { await p.getByRole("button", { name: "Review & publish" }).click(); await p.getByRole("tab", { name: "YAML diff" }).click(); await expect(p.getByText("intake.yaml", { exact: true })).toBeVisible(); await expect(p.getByText("policy.yaml", { exact: true })).toBeVisible(); } },
    { name: "publish", run: async (p) => { await p.getByRole("button", { name: "Publish", exact: true }).click(); await expect(p.locator(".toast", { hasText: "Published auto-intake" })).toBeVisible(); }, wait: 300 },
  ] },
];
