import type { Page } from "@playwright/test";
import { ngItem, settle, type Case, type Ctx } from "../cellKit";

/** The review page on the needs-gate item. `settings`: Diff settings rows to click first (saved to the mock's theme). */
async function ngReview(c: Ctx, opts: { sc?: string; tail?: string; side?: "pinned" | "rail"; settings?: string[]; then?: (p: Page) => Promise<void> } = {}) {
  await ngItem(c, opts.sc ?? "needs-gate", { tail: `/review${opts.tail ?? ""}`, side: opts.side });
  const p = c.page;
  if (opts.settings?.length) {
    await p.getByRole("button", { name: "Diff settings" }).click();
    for (const name of opts.settings) await p.getByRole("menuitemradio", { name }).or(p.getByRole("menuitemcheckbox", { name })).click();
    await p.keyboard.press("Escape");
    await settle(p, 300);
  }
  if (opts.then) { await opts.then(p); await settle(p, 400); }
}
/** Pick new line 5 of the review's file by its number, open the composer with Enter, type. */
async function ngComment(p: Page, text: string) {
  await p.getByRole("button", { name: "Pick new line 5", exact: true }).first().click();
  await p.getByRole("group", { name: /^Lines of / }).first().press("Enter");
  await p.getByRole("textbox", { name: "Comment" }).fill(text);
}

export const cells: Case[] = [
  // ux2-W8: the review page and the gate review overlay.
  { screen: "review", variant: "default", data: "default", widths: [1024, 1280, 1920], shells: [{ mode: "light" }, { short: true }], run: (c) => ngReview(c) },
  { screen: "review", variant: "default", data: "default", widths: [768], run: (c) => ngReview(c, { side: "rail" }) },
  { screen: "review", variant: "tree-open", data: "default", widths: [768], run: (c) => ngReview(c, { side: "rail", then: async (p) => { await p.getByRole("button", { name: "Expand file list" }).click(); } }) },
  { screen: "review", variant: "menu-from", data: "default", widths: [1280], run: (c) => ngReview(c, { then: async (p) => { await p.getByRole("button", { name: /^Compare from/ }).click(); } }) },
  { screen: "review", variant: "menu-nodes", data: "default", widths: [1280], run: (c) => ngReview(c, { then: async (p) => { await p.getByRole("button", { name: /^Nodes:/ }).click(); } }) },
  { screen: "review", variant: "menu-settings", data: "default", widths: [1280], run: (c) => ngReview(c, { then: async (p) => { await p.getByRole("button", { name: "Diff settings" }).click(); } }) },
  { screen: "review", variant: "split", data: "default", widths: [1280, 1920], run: (c) => ngReview(c, { settings: ["Side-by-side"] }) },
  { screen: "review", variant: "all-files", data: "default", widths: [1280], run: (c) => ngReview(c, { settings: ["Show one file at a time"] }) },
  { screen: "review", variant: "threads", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => ngReview(c, { settings: ["Show one file at a time"] }) },
  { screen: "review", variant: "composer", data: "default", widths: [1280], run: (c) => ngReview(c, { then: (p) => ngComment(p, "Name the fallback here, so the next reader does not have to find `default=0`.") }) },
  { screen: "review", variant: "suggest", data: "default", widths: [1280], run: (c) => ngReview(c, { then: async (p) => { await ngComment(p, "Say what it returns:"); await p.getByRole("button", { name: "± Suggest change" }).click(); } }) },
  { screen: "review", variant: "finish", data: "default", widths: [1280, 1920], shells: [{ mode: "light" }], run: (c) => ngReview(c, { then: async (p) => { await p.getByRole("button", { name: "Request changes" }).click(); } }) },
  { screen: "review", variant: "finish-gateless", data: "default", widths: [1280], run: (c) => ngReview(c, { sc: "running", then: async (p) => { await p.getByRole("button", { name: "Finish review" }).click(); await p.getByText("Why this node").waitFor(); } }) },
  { screen: "review", variant: "long", data: "long", widths: [1280, 1920], run: (c) => ngReview(c, { settings: ["Show one file at a time"] }) },
  // The gate review overlay: the needs-gate item's document beside its changes (a draft pending, a must-fix open).
  { screen: "gate-review", variant: "default", data: "default", widths: [1024, 1280, 1920], shells: [{ mode: "light" }], run: (c) => ngReview(c, { tail: "?doc=1", then: async (p) => { await p.getByText("WAITING FOR YOU").waitFor(); } }) },
  { screen: "gate-review", variant: "default", data: "default", widths: [768], run: (c) => ngReview(c, { tail: "?doc=1", side: "rail", then: async (p) => { await p.getByText("WAITING FOR YOU").waitFor(); } }) },
];
