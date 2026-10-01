import { ngItem, settle, type Case, type Ctx } from "../cellKit";

type Limit = { path: string; key: string; value: number; maximum: number | null };
const ATTEMPTS: Limit = { path: "verification", key: "max_attempts", value: 3, maximum: 5 };
const TIME: Limit = { path: "", key: "time_cap_minutes", value: 480, maximum: null };

/** The capped item at /ng, its stop naming `limit` (none: a cap stop that names no limit to raise), optionally the editor open. */
async function capped(c: Ctx, limit: Limit | null, then?: (p: Ctx["page"]) => Promise<void>) {
  const stop = c.S.bundles[c.S.ng.capped].item.stop as { limit?: Limit };
  if (limit) stop.limit = limit; else delete stop.limit;
  await ngItem(c, "capped", { then: async (p) => {
    await p.getByRole("status").filter({ hasText: "hit its" }).waitFor({ timeout: 4000 });
    if (then) await then(p);
  } });
}
const open = async (p: Ctx["page"]) => { await p.getByRole("button", { name: "Raise cap" }).click(); await p.getByRole("dialog").waitFor({ timeout: 4000 }); await settle(p, 300); };
const refused = async (p: Ctx["page"]) => {
  await p.route("**/api/work-items/*", (r) => r.request().method() === "PATCH" ? r.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: "policy.paths.verification.max_attempts: 6 exceeds the maximum 5" }) }) : r.fallback());
  await open(p);
  const input = p.getByRole("spinbutton"); await input.fill("6");
  await p.getByRole("button", { name: "Save & retry" }).click();
  await p.getByRole("alert").waitFor({ timeout: 4000 });
};

export const cells: Case[] = [
  // The cap banner: with a limit to raise ("Raise cap") and without ("Open config").
  { screen: "ng-item-cap", variant: "no-limit", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => capped(c, null) },
  { screen: "ng-item-cap", variant: "no-limit", data: "default", widths: [768], run: (c) => capped(c, null) },
  { screen: "ng-item-cap", variant: "limit", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => capped(c, ATTEMPTS) },
  { screen: "ng-item-cap", variant: "limit-long", data: "long", widths: [1280], run: (c) => capped(c, ATTEMPTS) },
  // The editor: a fix loop's attempts with a maximum, the work item's time cap with none, a refusal inline.
  { screen: "ng-item-cap", variant: "editor-attempts", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => capped(c, ATTEMPTS, open) },
  { screen: "ng-item-cap", variant: "editor-attempts", data: "default", widths: [768, 1024], run: (c) => capped(c, ATTEMPTS, open) },
  { screen: "ng-item-cap", variant: "editor-time", data: "default", widths: [1280], run: (c) => capped(c, TIME, open) },
  { screen: "ng-item-cap", variant: "editor-refused", data: "default", widths: [1280], shells: [{ mode: "light" }], run: (c) => capped(c, ATTEMPTS, refused) },
];
