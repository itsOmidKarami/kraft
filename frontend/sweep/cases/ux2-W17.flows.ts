import { expect, type Page, type Request } from "@playwright/test";
import { settle, type Flow } from "../flowKit";
import { NG_NOW } from "../ngItems";
import { SCREENS, type Card, type Tap } from "../../src/ng/phone/screens";

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
    { name: "escape-closes", run: async (p) => { await p.getByRole("button", { name: "Pause", exact: true }).click(); await expect(p.getByRole("dialog")).toBeVisible(); await p.getByRole("dialog").getByRole("button", { name: "Cancel" }).click(); await expect(p.getByRole("dialog")).toHaveCount(0); } },
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

  // P.1: every screen of the phone's list, reached by tapping from the board. One row per screen is the recorded fact.
  { name: "ng-phone-reach", widths: [390], mock: { ngBoard: true, areas: "default", harnesses: "floor", ngLibrary: "draft", apply: "none" }, start: start(() => "/ng/"), steps: SCREENS.map((s) => ({
    name: s.id,
    run: async (p: Page) => {
      await p.goto("/ng/");
      await p.locator("main h1").first().waitFor({ timeout: 8000 });
      await settle(p, 400);
      for (const t of s.taps) await tapOf(p, t);
      const want = new RegExp(`^/ng${s.route.replace(/:[^/]+/g, "[^/]+")}/?$`);
      await expect.poll(() => new URL(p.url()).pathname, { timeout: 6000 }).toMatch(want);
      await expect(p.locator("main h1").first()).toBeVisible();
      if (s.heading) await expect(p.getByRole("heading", { level: 1, name: s.heading })).toBeVisible();
    },
  })) },

  // P.2: Policy: edit a number, the dirty bar, Review & publish, Publish, and the chip says published.
  { name: "ng-phone-area-publish", widths: [390], mock: { areas: "default", apply: "none" }, start: start(() => "/ng/settings/policy/housekeeping"), steps: [
    { name: "edit-a-value", run: async (p) => { watch(p); await p.getByRole("button", { name: /^max active items/ }).click(); await p.locator("input[aria-label='max active items']").fill("7"); await p.keyboard.press("Enter"); await expect(p.getByRole("button", { name: "Review & publish" })).toBeVisible(); await expect(p.locator(".ph-status")).toContainText(/draft/i); } },
    { name: "review-sheet", run: async (p) => { await p.getByRole("button", { name: "Review & publish" }).click(); await expect(p.getByRole("dialog", { name: "Review & publish" })).toBeVisible(); await expect(p.getByRole("button", { name: "Publish", exact: true })).toBeEnabled(); } },
    { name: "publish", run: async (p) => { await p.getByRole("button", { name: "Publish", exact: true }).click(); await expect.poll(() => calls(p).filter((c) => /\/drafts\/policy\/policy\/publish$/.test(c)).length).toBe(1); } },
    { name: "chip-says-published", run: async (p) => { await expect(p.getByRole("dialog")).toHaveCount(0); await expect(p.locator(".ph-status")).toHaveText(/published/i); await expect(p.getByRole("button", { name: "Review & publish" })).toHaveCount(0); } },
  ] },

  // P.2: a publish that finds the file changed on disk shows the files, and the person decides.
  { name: "ng-phone-area-stale", widths: [390], mock: { areas: "stale", apply: "none" }, start: start(() => "/ng/settings/policy/housekeeping"), steps: [
    { name: "edit-and-publish", run: async (p) => { watch(p); await p.getByRole("button", { name: /^max active items/ }).click(); await p.locator("input[aria-label='max active items']").fill("7"); await p.keyboard.press("Enter"); await p.getByRole("button", { name: "Review & publish" }).click(); await p.getByRole("button", { name: "Publish", exact: true }).click(); } },
    { name: "409-shows-the-files", run: async (p) => { await expect(p.getByText(/changed on disk after this draft began/)).toBeVisible(); await expect(p.getByRole("button", { name: "Keep my version and publish" })).toBeVisible(); } },
    { name: "discard-instead", run: async (p) => { await p.getByRole("dialog").getByRole("button", { name: "Cancel" }).click(); await expect(p.getByRole("dialog")).toHaveCount(0); await p.getByRole("button", { name: "Discard", exact: true }).click(); await p.getByRole("button", { name: "Discard draft" }).click(); await expect(p.getByRole("dialog")).toHaveCount(0); await expect.poll(() => calls(p).some((c) => /^DELETE \/drafts\/policy\/policy$/.test(c))).toBe(true); } },
  ] },

  // P.2: Access: a pending restart, the confirm, the restart call, and the group gone once Kraft is back.
  { name: "ng-phone-access-restart", widths: [390], mock: { apply: "restart" }, start: start(() => "/ng/settings/access"), steps: [
    { name: "restart-group-shows", run: async (p) => { watch(p); await expect(p.getByRole("region", { name: "Restart needed" })).toBeVisible(); await expect(p.getByRole("button", { name: /^Restart Kraft/ })).toBeVisible(); } },
    { name: "change-the-port", run: async (p) => { await p.getByRole("button", { name: /^port/ }).click(); await p.locator("input[aria-label='Port']").fill("9100"); await p.keyboard.press("Enter"); await expect.poll(() => calls(p).filter((c) => c === "PUT /access").length).toBe(1); } },
    { name: "confirm-first", run: async (p) => { await p.getByRole("button", { name: /^Restart Kraft/ }).click(); await expect(p.getByRole("dialog", { name: "Restart Kraft?" })).toBeVisible(); expect(calls(p).some((c) => c === "POST /apply/restart")).toBe(false); } },
    { name: "restart-is-posted", run: async (p) => { await p.getByRole("dialog").getByRole("button", { name: "Restart Kraft" }).click(); await expect.poll(() => calls(p).filter((c) => c === "POST /apply/restart").length).toBe(1); } },
    { name: "group-gone-after-the-reload", run: async (p) => { await expect(p.getByRole("region", { name: "Restart needed" })).toHaveCount(0, { timeout: 15000 }); } },
  ] },
];

/** One tap of a screen-list entry: a control by role and name, or the first match of a selector (a board card is found by the bead of the /ng board fixture it stands for). */
const CARD: Record<Card, string> = { running: "kraft-91bc", capped: "kraft-7d21", gate: "kraft-cb59", escalated: "kraft-2c77" };
async function tapOf(p: Page, t: Tap) {
  if ("role" in t) return void (await p.getByRole(t.role, { name: new RegExp(t.name, "i") }).first().click());
  const loc = t.card ? p.locator(t.css).filter({ hasText: CARD[t.card] }) : p.locator(t.css);
  await loc.first().click();
}
