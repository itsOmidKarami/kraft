import { expect, type Page, type Request } from "@playwright/test";
import { settle, type Flow } from "../flowKit";
import { NG_NOW } from "../ngItems";

/** UX V2 W17 flows: every one at 390, the clock fixed. Assertions inside a step throw, which the manifest records as that step's error. */
const start = (url: (S: any) => string) => async (p: Page, S: any) => {
  await p.clock.setFixedTime(new Date(NG_NOW));
  await p.goto(url(S));
  await p.locator("main h1").first().waitFor({ timeout: 8000 });
  await settle(p, 600);
};
const path = (p: Page) => new URL(p.url()).pathname + new URL(p.url()).search;
/** Every non-GET the page makes, in order, for a step to read. */
const watch = (p: Page) => { const seen: string[] = []; p.on("request", (r: Request) => { if (r.method() !== "GET") seen.push(`${r.method()} ${new URL(r.url()).pathname.replace(/^\/api/, "")}`); }); (p as any).__calls = seen; };
const calls = (p: Page): string[] => (p as any).__calls;
const back = (p: Page) => p.locator(".ph-back").first();

export const flows: Flow[] = [
  // A.3: the URL is the stack; Back goes to the parent and never loops or leaves the app.
  { name: "ng-phone-back", widths: [390], start: start(() => "/ng/"), steps: [
    { name: "board-to-item", run: async (p) => { await p.locator(".ph-card-main").first().click(); await expect(p).toHaveURL(/\/ng\/work-items\/[^/?]+$/); } },
    { name: "item-to-node", run: async (p) => { await p.locator(".ph-node-row").first().click(); await expect(p).toHaveURL(/\/nodes\//); } },
    { name: "back-to-item", run: async (p) => { await back(p).click(); await expect(p).toHaveURL(/\/ng\/work-items\/[^/?]+$/); } },
    { name: "back-to-board", run: async (p) => { await back(p).click(); await expect(p).toHaveURL(/\/ng\/?$/); await expect(p.locator(".ph-card").first()).toBeVisible(); } },
    { name: "deep-link-to-a-task", run: async (p, S) => { await p.goto(`/ng/work-items/${S.ng.running}/nodes/verification?sel=verification.review.code_review`); await expect(p.getByRole("heading", { level: 1, name: "code_review" })).toBeVisible(); } },
    { name: "deep-back-1-node", run: async (p) => { await back(p).click(); await expect(p).toHaveURL(/nodes\/verification$/); } },
    { name: "deep-back-2-item", run: async (p, S) => { await back(p).click(); await expect(p).toHaveURL(new RegExp(`/ng/work-items/${S.ng.running}$`)); } },
    { name: "deep-back-3-board", run: async (p) => { await back(p).click(); await expect(p).toHaveURL(/\/ng\/?$/); await expect(p.locator(".ph-back")).toHaveCount(0); } },
    { name: "tab-press-resets", run: async (p) => { await p.getByRole("link", { name: /Search/ }).click(); await expect(p).toHaveURL(/\/ng\/search$/); await p.getByRole("link", { name: /^Board/ }).click(); await expect(p).toHaveURL(/\/ng\/?$/); } },
  ] },

  // A.5: Back closes the sheet and not the screen.
  { name: "ng-phone-sheet", widths: [390], state: "running", start: start((S) => `/ng/work-items/${S.ng.running}`), steps: [
    { name: "open-pause", run: async (p) => { await p.getByRole("button", { name: "Pause", exact: true }).click(); await expect(p.getByRole("dialog", { name: "Pause this item?" })).toBeVisible(); } },
    { name: "back-closes-the-sheet", run: async (p, S) => { await p.goBack(); await expect(p.getByRole("dialog")).toHaveCount(0); await expect(p).toHaveURL(new RegExp(`/ng/work-items/${S.ng.running}$`)); } },
    { name: "reopen-and-cancel", run: async (p) => { await p.getByRole("button", { name: "Pause", exact: true }).click(); await expect(p.getByRole("dialog")).toBeVisible(); await p.getByRole("button", { name: "Cancel" }).click(); await expect(p.getByRole("dialog")).toHaveCount(0); } },
    { name: "escape-closes", run: async (p) => { await p.getByRole("button", { name: "Pause", exact: true }).click(); await expect(p.getByRole("dialog")).toBeVisible(); await p.keyboard.press("Escape"); await expect(p.getByRole("dialog")).toHaveCount(0); } },
    { name: "confirm-pauses", run: async (p) => { watch(p); await p.getByRole("button", { name: "Pause", exact: true }).click(); await p.getByRole("button", { name: "Pause now" }).click(); await expect.poll(() => calls(p)).toContain(`POST /work-items/${(await p.evaluate(() => location.pathname)).split("/").pop()}/pause`); } },
  ] },

  // C.8 / R66: Steer on a running item pauses it, then resumes it with the note, in that order.
  { name: "ng-phone-steer-running", widths: [390], start: start((S) => `/ng/work-items/${S.ng.running}?compose=steer`), steps: [
    { name: "says-it-pauses", run: async (p) => { watch(p); await expect(p.getByText(/Steering pauses the item/)).toBeVisible(); await expect(p.getByRole("button", { name: "Send steer" })).toBeDisabled(); } },
    { name: "send-pauses-then-resumes", run: async (p, S) => {
      await p.getByRole("textbox", { name: "Your note" }).fill("skip the flaky suite"); await p.getByRole("button", { name: "Send steer" }).click();
      const id = S.ng.running;
      await expect.poll(() => calls(p)).toEqual([`POST /work-items/${id}/pause`, `POST /work-items/${id}/resume`]);
    } },
    { name: "back-on-the-item", run: async (p, S) => { await expect(p).toHaveURL(new RegExp(`/ng/work-items/${S.ng.running}$`)); } },
  ] },

  // F.3: Approve from a board row, and from the gate review.
  { name: "ng-phone-gate-approve", widths: [390], mock: { ngBoard: true }, start: start(() => "/ng/"), steps: [
    { name: "row-approve", run: async (p) => { watch(p); await p.getByRole("button", { name: "Approve", exact: true }).first().click(); await expect.poll(() => calls(p).filter((c) => /\/approve$/.test(c)).length).toBeGreaterThan(0); } },
    { name: "open-the-gate-review", run: async (p, S) => { await p.goto(`/ng/work-items/${S.ng["needs-gate"]}/review`); await expect(p.getByRole("button", { name: "Approve", exact: true })).toBeVisible(); } },
  ] },

  // A.1: the same URL in either shape; a resize swaps the app and keeps the place.
  { name: "ng-phone-resize", widths: [390], start: start((S) => `/ng/work-items/${S.ng.running}/nodes/verification`), steps: [
    { name: "phone-node", run: async (p) => { await expect(p.locator(".ph-tabs, .ph-strip").first()).toBeVisible(); } },
    { name: "desktop-shows-the-same-node", run: async (p) => { await p.setViewportSize({ width: 1280, height: 800 }); await expect(p.locator(".ng-sidebar")).toBeVisible({ timeout: 8000 }); expect(path(p)).toMatch(/nodes\/verification$/); } },
    { name: "back-to-phone", run: async (p) => { await p.setViewportSize({ width: 390, height: 844 }); await expect(p.locator(".ph-strip")).toBeVisible({ timeout: 8000 }); expect(path(p)).toMatch(/nodes\/verification$/); } },
  ] },

  // The redirect is gone: /ng at 390 stays on /ng.
  { name: "ng-phone-no-redirect", widths: [390], start: async (p) => { await p.goto("/ng"); await p.locator("main h1").first().waitFor({ timeout: 8000 }); await settle(p, 600); }, steps: [
    { name: "stays-on-ng", run: async (p) => { expect(new URL(p.url()).pathname.replace(/\/$/, "")).toBe("/ng"); await expect(p.getByRole("navigation", { name: "Primary" })).toBeVisible(); } },
    { name: "a-deep-link-too", run: async (p, S) => { await p.goto(`/ng/work-items/${S.ng.running}`); await expect(p).toHaveURL(new RegExp(`/ng/work-items/${S.ng.running}$`)); } },
  ] },
];
