import { expect } from "@playwright/test";
import { chains, type Flow } from "../flowKit";

export const flows: Flow[] = [
  // W10 B, D: a seam by keyboard → Exec node → its id → Create & open → the new node's empty view.
  { name: "ng-chain-add-node", widths: [1280], start: chains("default"), steps: [
    { name: "seam-menu", run: async (p) => { await p.locator(".seam").nth(2).focus(); await p.keyboard.press("Enter"); await expect(p.getByRole("menuitem", { name: /Exec node/ })).toBeFocused(); }, kbd: true },
    { name: "exec-node", run: async (p) => { await p.keyboard.press("Enter"); await expect(p.getByRole("textbox", { name: "Node id" })).toBeFocused(); }, kbd: true },
    { name: "taken-id", run: async (p) => { await p.keyboard.type("spec"); await expect(p.getByRole("alert")).toHaveText("spec is taken."); await expect(p.getByRole("button", { name: "Create & open →" })).toBeDisabled(); }, kbd: true },
    { name: "create", run: async (p) => { for (let i = 0; i < 4; i++) await p.keyboard.press("Backspace"); await p.keyboard.type("lint"); await p.keyboard.press("Enter"); await expect.poll(() => new URL(p.url()).pathname).toBe("/ng/templates/chains/default/nodes/lint"); }, kbd: true, wait: 600 },
    { name: "empty-node", run: async (p) => { await expect(p.getByText(/This node is empty/)).toBeVisible(); await expect(p.getByRole("button", { name: "add your first step" })).toBeVisible(); } },
  ] },
  // W10 F: Review & publish, then a stale draft's 409 with the server's diff.
  { name: "ng-chain-publish", widths: [1280], start: chains("default"), steps: [
    { name: "review", run: async (p) => { await p.getByRole("button", { name: "Review & publish" }).click(); await expect(p.getByRole("heading", { name: /^Draft · \d+ changes?$/ })).toBeVisible(); } },
    { name: "yaml-diff", run: async (p) => { await p.getByRole("tab", { name: "YAML diff" }).click(); await expect(p.locator(".tpl-rv-line.is-add").first()).toBeVisible(); } },
    { name: "publish", run: async (p) => { await p.getByRole("button", { name: "Publish", exact: true }).click(); await expect(p.locator(".toast", { hasText: "Published default" })).toBeVisible(); }, wait: 300 },
    { name: "stale-chain", run: async (p) => { await chains("stale")(p); await p.getByRole("button", { name: "Review & publish" }).click(); } },
    { name: "stale-publish", run: async (p) => { await p.getByRole("button", { name: "Publish", exact: true }).click(); await expect(p.getByText("Published since this draft began", { exact: true })).toBeVisible(); await expect(p.getByRole("button", { name: "Keep my version and publish" })).toBeVisible(); } },
  ] },
  // W10 H: the YAML view, an edit there, and back to the canvas.
  { name: "ng-chain-yaml", widths: [1280], start: chains("default"), steps: [
    { name: "open-yaml", run: async (p) => { await p.getByRole("button", { name: "YAML", exact: true }).click(); await expect(p.getByRole("textbox", { name: "chains/default.yaml, YAML" })).toBeVisible(); } },
    { name: "type", run: async (p) => { const ta = p.getByRole("textbox", { name: "chains/default.yaml, YAML" }); await ta.click(); await p.keyboard.press("End"); await p.keyboard.type(" "); await expect(ta).toBeFocused(); }, kbd: true, wait: 600 },
    { name: "escape-leaves", run: async (p) => { await p.keyboard.press("Escape"); await expect(p.getByRole("textbox", { name: "chains/default.yaml, YAML" })).not.toBeFocused(); }, kbd: true },
    { name: "back-to-canvas", run: async (p) => { await p.getByRole("button", { name: "⇄ Canvas" }).click(); await expect(p.locator(".canvas").first()).toBeVisible(); } },
  ] },
  // W10 I: switching from a chain with a draft asks first; Stay stays.
  { name: "ng-chain-switch-guard", widths: [1280], start: chains("default"), steps: [
    { name: "switcher", run: async (p) => { await p.getByRole("button", { name: "Chain default, switch chain" }).click(); await expect(p.getByRole("option").first()).toBeVisible(); } },
    { name: "pick-other", run: async (p) => { await p.getByRole("option").filter({ hasNotText: /^default/ }).first().click(); await expect(p.getByRole("dialog", { name: "You have unpublished changes" })).toBeVisible(); } },
    { name: "stay", run: async (p) => { await p.getByRole("button", { name: "Stay" }).click(); await expect(p.getByRole("dialog", { name: "You have unpublished changes" })).toHaveCount(0); expect(new URL(p.url()).pathname).toBe("/ng/templates/chains/default"); } },
  ] },
];
