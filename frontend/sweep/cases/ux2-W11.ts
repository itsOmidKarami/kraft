import { ngItem, type Case } from "../cellKit";

export const cells: Case[] = [
  // ux2-W11: the item chain draft on the running item (the run stands on verification), seeded by `mock: { itemDraft }`.
  { screen: "ng-item-draft", variant: "changes", data: "default", widths: [1024, 1280, 1920], shells: [{ mode: "light" }], mock: { itemDraft: "changes" }, run: (c) => ngItem(c, "running") },
  { screen: "ng-item-draft", variant: "problems", data: "default", widths: [1024, 1280], mock: { itemDraft: "problems" }, run: (c) => ngItem(c, "running") },
  { screen: "ng-item-draft", variant: "passed", data: "default", widths: [1280], mock: { itemDraft: "passed" }, run: (c) => ngItem(c, "running") },
  { screen: "ng-item-draft", variant: "apply", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], mock: { itemDraft: "changes" }, run: (c) => ngItem(c, "running", { then: async (p) => { await p.getByRole("button", { name: "Review & apply" }).click(); await p.getByRole("dialog").waitFor(); } }) },
  { screen: "ng-item-draft", variant: "apply-blocked", data: "default", widths: [1280], mock: { itemDraft: "problems" }, run: (c) => ngItem(c, "running", { then: async (p) => { await p.getByRole("button", { name: "DRAFT · 1 CHANGE" }).click(); await p.getByRole("dialog").waitFor(); } }) },
  { screen: "ng-item-draft", variant: "seam-menu", data: "default", widths: [1280], mock: { itemDraft: "none" }, run: (c) => ngItem(c, "running", { then: async (p) => { await p.getByRole("button", { name: "Add a library node here" }).first().focus(); await p.keyboard.press("Enter"); await p.getByRole("option", { name: /post_draft_feedback/ }).waitFor(); } }) },
  { screen: "ng-item-draft", variant: "config-edit", data: "default", widths: [1280], mock: { itemDraft: "changes" }, run: (c) => ngItem(c, "running", { tail: "?sel=merge_request.open.open_draft&tab=config", then: async (p) => { await p.getByRole("button", { name: "Override effort" }).click(); } }) },
  { screen: "ng-item-draft", variant: "applied", data: "default", widths: [1280], mock: { itemDraft: "applied" }, run: (c) => ngItem(c, "running", { tail: "?tab=config", then: async (p) => { await p.getByText("applied by the draft").first().waitFor(); } }) },
  { screen: "ng-item-draft", variant: "leave", data: "default", widths: [1280], mock: { itemDraft: "changes" }, run: (c) => ngItem(c, "running", { then: async (p) => { await p.getByRole("link", { name: "Board" }).first().click(); await p.getByRole("dialog").waitFor(); } }) },
];
