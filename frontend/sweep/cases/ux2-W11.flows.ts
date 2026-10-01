import { expect } from "@playwright/test";
import { NG_NOW } from "../ngItems";
import { ng, ngItem, type Flow } from "../flowKit";

export const flows: Flow[] = [
  // ux2-W11 C: a + seam after the current node by keyboard → a library node → its id → Create & open → it lands in the draft.
  { name: "ng-item-draft-add-node", widths: [1280], keyboard: true, mock: { itemDraft: "none" }, start: ngItem("running"), steps: [
    { name: "seam", run: async (p) => { await p.getByRole("button", { name: "Add a library node here" }).first().focus(); await p.keyboard.press("Enter"); await expect(p.getByRole("textbox", { name: "Search library nodes" })).toBeFocused(); }, kbd: true },
    { name: "pick", run: async (p) => { await p.keyboard.type("feedback"); await p.keyboard.press("Enter"); await expect(p.getByRole("textbox", { name: "Node id" })).toHaveValue("post_draft_feedback"); }, kbd: true },
    { name: "create", run: async (p) => { await p.keyboard.press("Enter"); await expect(p.getByText("DRAFT · 1 CHANGE")).toBeVisible(); await expect(p.getByRole("complementary", { name: "post_draft_feedback pane" })).toBeVisible(); await expect(p.getByText(/Added in this item's draft/)).toBeVisible(); } },
  ] },
  // ux2-W11 D, F, G: override a later task's effort from its Config, Review & apply, Apply; the chain pane then lists it as changed for this item.
  { name: "ng-item-draft-apply", widths: [1280], mock: { itemDraft: "none" }, start: async (p, S) => { await p.clock.setFixedTime(new Date(NG_NOW)); await ng(`/ng/work-items/${S.ng.running}?sel=merge_request.open.open_draft&tab=config`)(p); }, steps: [
    { name: "override", run: async (p) => { await p.getByRole("button", { name: "Override model" }).click(); await p.getByRole("textbox", { name: "model" }).fill("opus"); await p.keyboard.press("Enter"); await expect(p.getByText("DRAFT · 1 CHANGE")).toBeVisible(); await expect(p.getByRole("button", { name: "Reset model" })).toBeVisible(); } },
    { name: "review", run: async (p) => { await p.getByRole("button", { name: "Review & apply" }).click(); await expect(p.getByRole("dialog", { name: "Apply 1 change to this item?" })).toBeVisible(); await expect(p.getByText("✓ resolves")).toBeVisible(); } },
    { name: "apply", run: async (p) => { await p.getByRole("button", { name: "Apply", exact: true }).click(); await expect(p.locator(".toast", { hasText: "Applied 1 change" })).toBeVisible(); await expect(p.getByText("DRAFT · 1 CHANGE")).toHaveCount(0); } },
    { name: "changed-for-this-item", run: async (p) => { await p.goto(p.url().replace(/\?.*$/, "?tab=config")); await expect(p.getByText("applied by the draft")).toBeVisible(); await expect(p.getByText("model opus")).toBeVisible(); }, wait: 400 },
  ] },
  // ux2-W11 H: a draft, a click on Board → the leave dialog → Stay keeps the item.
  { name: "ng-item-draft-leave", widths: [1280], mock: { itemDraft: "changes" }, start: ngItem("running"), steps: [
    { name: "leave", run: async (p) => { await p.getByRole("link", { name: "Board" }).first().click(); await expect(p.getByRole("dialog", { name: "You have unapplied changes to this item" })).toBeVisible(); await expect(p).toHaveURL(/\/work-items\//); } },
    { name: "stay", run: async (p) => { await p.getByRole("button", { name: "Stay" }).click(); await expect(p.getByRole("dialog")).toHaveCount(0); await expect(p.getByText("DRAFT · 3 CHANGES")).toBeVisible(); } },
  ] },
];
