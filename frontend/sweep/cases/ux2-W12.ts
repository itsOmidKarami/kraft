import { ngChains, settle, type Case, type Ctx } from "../cellKit";

/** The /ng Library (ux2-W12) on its draft fixture (`mock: { ngLibrary }`), sidebar pinned, once the list has drawn. */
async function ngLibrary(c: Ctx, ref = "") {
  await c.page.addInitScript(() => localStorage.setItem("kraft.sidebar.v2", "pinned"));
  await c.page.goto(`/ng/templates/library${ref ? `/${ref}` : ""}`);
  await c.page.locator(".lib-row, .lib-note").first().waitFor({ timeout: 8000 });
  if (ref) await c.page.locator(".pane").first().waitFor({ timeout: 8000 });
  await settle(c.page, 700);
}

export const cells: Case[] = [
  { screen: "ng-library", variant: "list", data: "default", widths: [1024, 1280, 1920], shells: [{ mode: "light" }], mock: { ngLibrary: "draft" }, run: (c) => ngLibrary(c) },
  { screen: "ng-library", variant: "list-blocked", data: "default", widths: [1280], mock: { ngLibrary: "blocked" }, run: (c) => ngLibrary(c) },
  { screen: "ng-library", variant: "task", data: "default", widths: [1024, 1280, 1920], shells: [{ mode: "light" }], mock: { ngLibrary: "blocked" }, run: (c) => ngLibrary(c, "tasks.implementer") },
  { screen: "ng-library", variant: "node", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], mock: { ngLibrary: "draft" }, run: async (c) => {
    await ngLibrary(c, "nodes.verification");
    // The fix loop's tab opens the bottom pane on it and picks the loop.
    await c.page.getByRole("tab", { name: "Fix loop" }).click();
    await settle(c.page, 500);
  } },
  { screen: "ng-library", variant: "gate", data: "default", widths: [1280], mock: { ngLibrary: "draft" }, run: (c) => ngLibrary(c, "nodes.approval") },
  { screen: "ng-library", variant: "step", data: "default", widths: [1280], mock: { ngLibrary: "draft" }, run: (c) => ngLibrary(c, "steps.checks") },
  { screen: "ng-library", variant: "task-glyph", data: "default", widths: [1280], mock: { ngLibrary: "draft" }, run: (c) => ngLibrary(c, "tasks.fixer") },
  { screen: "ng-library", variant: "steering", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], mock: { ngLibrary: "draft" }, run: async (c) => {
    await ngLibrary(c, "steering.project-standards");
    await c.page.locator(".lib-section-row").first().waitFor({ timeout: 8000 });
    await c.page.locator(".lib-section-row").nth(4).click();
    await settle(c.page, 300);
  } },
  { screen: "ng-library", variant: "review", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], mock: { ngLibrary: "blocked" }, run: async (c) => {
    await ngLibrary(c);
    await c.page.getByRole("button", { name: /Review & publish/ }).click();
    await c.page.getByRole("heading", { name: /Draft ·/ }).waitFor({ timeout: 8000 });
    await settle(c.page, 300);
  } },
  { screen: "ng-library", variant: "review-clean", data: "default", widths: [1280], mock: { ngLibrary: "draft" }, run: async (c) => {
    await ngLibrary(c);
    await c.page.getByRole("button", { name: /Review & publish/ }).click();
    await c.page.getByRole("heading", { name: /Draft ·/ }).waitFor({ timeout: 8000 });
    await settle(c.page, 300);
  } },
  { screen: "ng-library", variant: "task-config", data: "default", widths: [1280], mock: { ngLibrary: "draft" }, run: async (c) => {
    await ngLibrary(c, "tasks.implementer");
    await c.page.getByRole("tab", { name: "Config" }).click();
    await settle(c.page, 300);
  } },
  { screen: "ng-chains", variant: "move-to-library", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "spec, node" }).click();
    await c.page.getByRole("button", { name: "Move to library…" }).click();
    await c.page.getByRole("dialog", { name: "Move to library" }).waitFor({ timeout: 8000 });
    await settle(c.page, 400);
  } },
];
