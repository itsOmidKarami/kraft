import { settle, ngChains, type Case } from "../cellKit";

export const cells: Case[] = [
  // W10: the Chains editor on the mock's real draft answers (sweep/draftViews.json).
  { screen: "chains", variant: "canvas", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: (c) => ngChains(c, "default") },
  { screen: "chains", variant: "pane-gate", data: "default", widths: [1280], shells: [{ mode: "light" }], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "spec_approval, gate" }).click();
    await settle(c.page, 300);
  } },
  { screen: "chains", variant: "pane-config", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "implementation, node" }).click();
    await c.page.getByRole("tab", { name: "Config" }).click();
    await settle(c.page, 300);
  } },
  { screen: "chains", variant: "node", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: (c) => ngChains(c, "default", "verification") },
  { screen: "chains", variant: "node-task", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default", "verification");
    await c.page.getByRole("button", { name: "code_review, agent task" }).click();
    await settle(c.page, 300);
  } },
  { screen: "chains", variant: "node-empty", data: "default", widths: [1280], run: (c) => ngChains(c, "broken", "lint") },
  { screen: "chains", variant: "gate", data: "default", widths: [1280], run: (c) => ngChains(c, "default", "spec_approval") },
  { screen: "chains", variant: "task-menu", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default", "verification");
    await c.page.getByRole("button", { name: "Add a parallel task" }).first().click();
    await c.page.getByRole("menuitem", { name: "From the library…" }).waitFor({ timeout: 4000 });
    await settle(c.page, 300);
  } },
  { screen: "chains", variant: "bottom", data: "default", widths: [1280], shells: [{ mode: "light" }], run: async (c) => {
    await ngChains(c, "default", "verification");
    await c.page.getByRole("tab", { name: "Fix loop" }).click();
    await settle(c.page, 400);
  } },
  { screen: "chains", variant: "bottom-empty", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default", "verification");
    await c.page.getByRole("tab", { name: "Escalation" }).click();
    await settle(c.page, 400);
  } },
  { screen: "chains", variant: "bottom-handler", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default", "merge_request_feedback");
    await c.page.getByRole("tab", { name: "On failure" }).click();
    await settle(c.page, 400);
  } },
  { screen: "chains", variant: "review", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "Review & publish" }).click();
    await settle(c.page, 500);
  } },
  { screen: "chains", variant: "review-yaml", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "Review & publish" }).click();
    await c.page.getByRole("tab", { name: "YAML diff" }).click();
    await settle(c.page, 500);
  } },
  { screen: "chains", variant: "review-stale", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "stale");
    await c.page.getByRole("button", { name: "Review & publish" }).click();
    await c.page.getByRole("button", { name: "Publish", exact: true }).click();
    await c.page.getByText("Published since this draft began", { exact: true }).waitFor({ timeout: 4000 });
    await settle(c.page, 300);
  } },
  { screen: "chains", variant: "review-problems", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "broken");
    await c.page.getByRole("button", { name: "Review & publish" }).click();
    await settle(c.page, 500);
  } },
  { screen: "chains", variant: "yaml", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "YAML", exact: true }).click();
    await settle(c.page, 500);
  } },
  { screen: "chains", variant: "problems", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "broken");
    await c.page.getByRole("button", { name: "YAML", exact: true }).click();
    await settle(c.page, 500);
  } },
  { screen: "chains", variant: "yaml-error", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "yaml-error");
    await c.page.getByRole("button", { name: "YAML", exact: true }).click();
    await settle(c.page, 500);
  } },
  { screen: "chains", variant: "item-yaml", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "spec_approval, gate" }).click();
    await c.page.getByRole("tab", { name: "YAML" }).click();
    await settle(c.page, 500);
  } },
  { screen: "chains", variant: "switcher", data: "default", widths: [1280], shells: [{ mode: "light" }], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "Chain default, switch chain" }).click();
    await c.page.getByRole("option").first().waitFor({ timeout: 4000 });
    await settle(c.page, 300);
  } },
  { screen: "chains", variant: "unsaved", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "Chain default, switch chain" }).click();
    await c.page.getByRole("option").filter({ hasNotText: /^default/ }).first().click();
    await c.page.getByRole("dialog", { name: "You have unpublished changes" }).waitFor({ timeout: 4000 });
    await settle(c.page, 300);
  } },
  { screen: "chains", variant: "rename", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    const spec = c.page.getByRole("button", { name: "spec, node" });
    await spec.click();
    await spec.click();
    await c.page.getByRole("dialog", { name: "Rename node" }).waitFor({ timeout: 4000 });
    await settle(c.page, 300);
  } },
  { screen: "chains", variant: "remove", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "spec, node" }).click();
    await c.page.getByRole("button", { name: "Remove node" }).click();
    await c.page.getByRole("dialog", { name: "Remove node" }).waitFor({ timeout: 4000 });
    // The pointer off the footer button: its hover tint is W1's (Kraft-xjv0h), not this cell's subject.
    await c.page.mouse.move(400, 600);
    await settle(c.page, 300);
  } },
  { screen: "chains", variant: "reorder", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    const b = (await c.page.getByRole("button", { name: "plan, node" }).boundingBox())!;
    await c.page.mouse.move(b.x + b.width / 2, b.y + 20);
    await c.page.mouse.down();
    await c.page.mouse.move(b.x + b.width / 2 + 120, b.y + 20, { steps: 6 });
    await settle(c.page, 500);
  } },
  { screen: "chains", variant: "change-base", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    // Under the pane at 1280: focus pans it into view, Enter opens its pane.
    await c.page.getByRole("button", { name: "verification, node" }).focus();
    await c.page.keyboard.press("Enter");
    await c.page.getByRole("button", { name: "Change base…" }).click();
    await c.page.getByRole("option").first().click();
    await c.page.getByRole("dialog", { name: /^Change base of/ }).waitFor({ timeout: 4000 });
    await settle(c.page, 300);
  } },
  { screen: "chains", variant: "icon-picker", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.getByRole("button", { name: "spec, node" }).click();
    await c.page.getByRole("button", { name: /^Icon.*, change$/ }).click();
    await c.page.getByRole("option").first().waitFor({ timeout: 6000 });
    await settle(c.page, 300);
  } },
  { screen: "chains", variant: "canvas-empty", data: "default", widths: [1280], run: (c) => ngChains(c, "empty") },
  { screen: "chains", variant: "seam-menu", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.locator(".seam").nth(3).click();
    await c.page.getByRole("menuitem", { name: /Exec node/ }).waitFor({ timeout: 4000 });
    await settle(c.page, 300);
  } },
  { screen: "chains", variant: "seam-id", data: "default", widths: [1280], run: async (c) => {
    await ngChains(c, "default");
    await c.page.locator(".seam").nth(3).click();
    await c.page.getByRole("menuitem", { name: /Exec node/ }).click();
    await c.page.keyboard.type("spec");
    await settle(c.page, 300);
  } },
];
