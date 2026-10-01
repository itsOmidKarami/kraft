import { expect, type Page } from "@playwright/test";
import { settle, chains, type Flow } from "../flowKit";

// ux2-W12: the Library on its draft fixture (`mock: { ngLibrary }`).
const library = (ref = "") => async (p: Page) => { await p.addInitScript(() => localStorage.setItem("kraft.sidebar.v2", "pinned")); await p.goto(`/ng/templates/library${ref ? `/${ref}` : ""}`); await p.locator(".lib-row").first().waitFor({ timeout: 8000 }); await settle(p, 700); };

export const flows: Flow[] = [
  // W12 H (R47): a component moves into the library, the review shows both files, and one publish writes both.
  { name: "ng-move-to-library", widths: [1280], start: chains("default"), steps: [
    { name: "select-node", run: async (p) => { await p.getByRole("button", { name: "spec, node" }).click(); await expect(p.getByRole("button", { name: "Move to library…" })).toBeVisible(); } },
    { name: "card", run: async (p) => { await p.getByRole("button", { name: "Move to library…" }).click(); await expect(p.getByText("The library gains nodes.spec (exec node, 2 keys).")).toBeVisible(); } },
    { name: "move", run: async (p) => { await p.keyboard.press("Enter"); await expect(p.locator(".toast", { hasText: "Moved to the library as nodes.spec" })).toBeVisible(); }, wait: 300 },
    { name: "review", run: async (p) => { await p.getByRole("button", { name: "Review & publish" }).click(); await expect(p.getByText("added to the library")).toBeVisible(); } },
    { name: "both-files", run: async (p) => { await p.getByRole("tab", { name: "YAML diff" }).click(); await expect(p.locator(".tpl-rv-file")).toHaveText(["chains/default.yaml", "library.yaml"]); } },
    { name: "publish", run: async (p) => { await p.getByRole("button", { name: "Publish", exact: true }).click(); await expect(p.locator(".toast", { hasText: "Published default and the library" })).toBeVisible(); }, wait: 300 },
  ] },
  // W12 F: a library edit that breaks a chain cannot publish, and names the chain, the path and the component.
  { name: "ng-library-blocked-publish", widths: [1280], mock: { ngLibrary: "blocked" }, start: library(), steps: [
    { name: "review", run: async (p) => { await p.getByRole("button", { name: /Review & publish/ }).click(); await expect(p.getByRole("heading", { name: /^Draft · \d+ changes?$/ })).toBeVisible(); } },
    { name: "names-the-chain", run: async (p) => { const row = p.locator(".tpl-rv-prob", { hasText: "whose profile 'strong' does not exist" }); await expect(row).toContainText("implementation.main.implement"); await expect(row).toContainText("breaks chain default"); await expect(row).toContainText("from tasks.implementer"); await expect(p.getByRole("button", { name: "Publish", exact: true })).toBeDisabled(); } },
    { name: "fix", run: async (p) => { await p.locator(".tpl-rv-prob", { hasText: "whose profile 'strong' does not exist" }).getByRole("button", { name: "Fix →" }).click(); await expect(p.getByRole("heading", { name: "implementer" })).toBeVisible(); expect(new URL(p.url()).pathname).toBe("/ng/templates/library/tasks.implementer"); } },
  ] },
  // W12 E: a steering profile's launch-order preview, a row opened.
  { name: "ng-library-steering-preview", widths: [1280], mock: { ngLibrary: "draft" }, start: library("steering.project-standards"), steps: [
    { name: "preview", run: async (p) => { await expect(p.locator(".lib-section-row").first()).toBeVisible(); await expect(p.getByText("Preview shows the published text. Publish to see this change.")).toBeVisible(); } },
    { name: "open-row", run: async (p) => { await p.locator(".lib-section-row").nth(4).click(); await expect(p.locator(".lib-section-text")).toBeVisible(); } },
    { name: "close-row", run: async (p) => { await p.locator(".lib-section-row").nth(4).click(); await expect(p.locator(".lib-section-text")).toHaveCount(0); } },
  ] },
  // W12 G: a rename from the title rewrites its references and the URL follows.
  { name: "ng-library-rename", widths: [1280], mock: { ngLibrary: "draft" }, start: library("tasks.fixer"), steps: [
    { name: "open-card", run: async (p) => { await p.getByRole("button", { name: "fixer", exact: true }).click(); await expect(p.getByRole("textbox", { name: "Rename task" })).toBeVisible(); } },
    { name: "rename", run: async (p) => { const f = p.getByRole("textbox", { name: "Rename task" }); await f.fill("repairer"); await p.keyboard.press("Enter"); await expect(p.locator(".toast", { hasText: "Renamed fixer → repairer" })).toBeVisible(); expect(new URL(p.url()).pathname).toBe("/ng/templates/library/tasks.repairer"); }, wait: 300 },
  ] },
];
