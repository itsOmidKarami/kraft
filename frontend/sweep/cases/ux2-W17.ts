import type { Page } from "@playwright/test";
import { NG_NOW } from "../ngItems";
import { settle, type Case, type Ctx } from "../cellKit";

/** UX V2 W17 (the phone): every cell is at 390 and `noLight`: a phone screen has no 1280 shape. */
const W = [390];

/** A phone screen at `url`, the clock fixed so elapsed and ages read the same every run, `then` for a sheet or a tab. */
async function ph(c: Ctx, url: string, then?: (p: Page) => Promise<void>) {
  await c.page.clock.setFixedTime(new Date(NG_NOW));
  await c.page.goto(url);
  await c.page.locator("main h1").first().waitFor({ timeout: 8000 });
  await settle(c.page, 700);
  if (then) { await then(c.page); await settle(c.page, 400); }
}
const click = (name: string | RegExp, role: "button" | "tab" | "link" | "radio" | "switch" = "button") => async (p: Page) => { await p.getByRole(role, { name }).first().click(); };
const item = (c: Ctx, sc: string, tail = "") => `/work-items/${c.S.ng[sc]}${tail}`;
const board = { ngBoard: true } as const;

const cell = (screen: string, variant: string, run: (c: Ctx) => Promise<void>, extra: Partial<Case> = {}): Case => ({ screen, variant, data: "default", widths: W, noLight: true, run, ...extra });

export const cells: Case[] = [
  // B: the board.
  cell("phone-board", "default", (c) => ph(c, "/"), { mock: board }),
  cell("phone-board", "needs", (c) => ph(c, "/?f=needs"), { mock: board }),
  cell("phone-board", "running", (c) => ph(c, "/?f=running"), { mock: board }),
  cell("phone-board", "done", (c) => ph(c, "/?f=done"), { mock: board }),
  cell("phone-board", "repo-sheet", (c) => ph(c, "/", click(/All repos/)), { mock: board }),
  cell("phone-board", "long", (c) => ph(c, "/"), { data: "long", mock: board }),
  cell("phone-board", "many", (c) => ph(c, "/"), { data: "many", mock: board }),
  cell("phone-board", "empty", (c) => ph(c, "/"), { data: "empty", mock: { ngBoard: "empty" } }),
  cell("phone-board", "offline", async (c) => {
    await c.page.route(/\/api\/work-items(\?|$)/, (r) => r.abort());
    await ph(c, "/");
  }, { mock: board }),

  // C: the item screen, by state, then its sheets.
  ...(["gate:needs-gate", "running:running", "capped:capped", "question:needs-you", "escalated:escalated", "failed:failed", "waiting:waiting", "conflict:conflict", "mr-closed:mr-closed", "paused:paused", "done:done", "cancelled:cancelled", "archived:archived", "worker-lost:worker-lost"] as const).map((s) => {
    const [variant, sc] = s.split(":");
    return cell("phone-item", variant, (c) => ph(c, item(c, sc)));
  }),
  cell("phone-item", "long", (c) => ph(c, item(c, "running")), { data: "long" }),
  cell("phone-item", "pause-sheet", (c) => ph(c, item(c, "running"), click("Pause"))),
  cell("phone-item", "kebab", (c) => ph(c, item(c, "running"), click("More actions"))),
  // The ng "capped" item stopped at a cap, which has no raise (Kraft-x8qzu); a budget stop does, so this cell makes it one.
  cell("phone-item", "raise-sheet", async (c) => {
    const b = (c.S.bundles as any)[c.S.ng.capped].item;
    b.stop = { ...b.stop, kind: "budget", reason: "The budget ran out." };
    b.budget_cap = { cap_usd: 10, source: "item", spent_usd: 10 };
    await ph(c, item(c, "capped"), click(/Raise budget/));
  }),
  // R73: a cap stop whose stop.limit the server sends offers the raise; the cell sets it on the seeded item.
  cell("phone-item", "capped-limit", async (c) => {
    const b = (c.S.bundles as any)[c.S.ng.capped].item;
    b.stop = { ...b.stop, limit: { path: "", key: "time_cap_minutes", value: 480, maximum: 1440 } };
    await ph(c, item(c, "capped"));
  }),
  cell("phone-item", "capped-raise", async (c) => {
    const b = (c.S.bundles as any)[c.S.ng.capped].item;
    b.stop = { ...b.stop, limit: { path: "", key: "time_cap_minutes", value: 480, maximum: 1440 } };
    await ph(c, item(c, "capped"), click(/^Raise the running time cap/));
  }),
  cell("phone-composer", "steer-running", (c) => ph(c, item(c, "running", "?compose=steer"))),
  cell("phone-composer", "steer-paused", (c) => ph(c, item(c, "paused", "?compose=steer"))),
  cell("phone-composer", "reject", (c) => ph(c, item(c, "needs-gate", "?compose=reject"))),
  cell("phone-composer", "answer", (c) => ph(c, item(c, "needs-you", "?compose=answer"))),
  cell("phone-composer", "escalate", (c) => ph(c, item(c, "running", "?compose=escalate"))),
  cell("phone-composer", "cancel", (c) => ph(c, item(c, "running", "?compose=cancel"))),

  // D: the node screen.
  cell("phone-node", "running", (c) => ph(c, item(c, "running", "/nodes/verification"))),
  cell("phone-node", "paused", (c) => ph(c, item(c, "paused", "/nodes/verification"))),
  cell("phone-node", "failed", (c) => ph(c, item(c, "failed", "/nodes/verification"))),
  cell("phone-node", "done", (c) => ph(c, item(c, "running", "/nodes/plan"))),
  cell("phone-node", "todo", (c) => ph(c, item(c, "running", "/nodes/release"))),
  cell("phone-node", "gate-waiting", (c) => ph(c, item(c, "needs-gate", "/nodes/final_review"))),
  cell("phone-node", "gate-done", (c) => ph(c, item(c, "running", "/nodes/spec_approval"))),
  cell("phone-node", "log", (c) => ph(c, item(c, "running", "/nodes/verification?tab=log"))),
  cell("phone-node", "config", (c) => ph(c, item(c, "running", "/nodes/verification?tab=config"))),
  cell("phone-node", "long", (c) => ph(c, item(c, "running", "/nodes/verification")), { data: "long" }),

  // E: the task screen.
  cell("phone-task", "overview", (c) => ph(c, item(c, "running", "/nodes/verification?sel=verification.review.code_review"))),
  cell("phone-task", "log", (c) => ph(c, item(c, "running", "/nodes/verification?sel=verification.review.code_review&tab=log"))),
  cell("phone-task", "config", (c) => ph(c, item(c, "running", "/nodes/verification?sel=verification.review.code_review&tab=config"))),
  cell("phone-task", "first-attempt", (c) => ph(c, item(c, "running", "/nodes/verification?sel=verification.review.code_review&attempt=1"))),
  cell("phone-task", "not-started", (c) => ph(c, item(c, "running", "/nodes/release?sel=release.ship.release"))),
  cell("phone-task", "thread", (c) => ph(c, item(c, "escalated", "/nodes/implementation?sel=implementation.escalation.escalation"))),
  cell("phone-task", "long", (c) => ph(c, item(c, "running", "/nodes/verification?sel=verification.review.code_review")), { data: "long" }),

  // F: the gate review and a document.
  cell("phone-gate", "doc", (c) => ph(c, item(c, "needs-gate", "/review"))),
  cell("phone-gate", "file-open", (c) => ph(c, item(c, "needs-gate", "/review"), async (p) => { if (await p.locator(".ph-file-row").count()) await p.locator(".ph-file-row").first().click(); })),
  cell("phone-gate", "reject", (c) => ph(c, item(c, "needs-gate", "/review?compose=reject"))),
  cell("phone-gate", "long", (c) => ph(c, item(c, "needs-gate", "/review")), { data: "long" }),
  cell("phone-doc", "default", async (c) => {
    await c.page.goto("/");
    const id = c.S.ng["needs-gate"];
    const docs = await c.page.evaluate(async (i) => (await (await fetch(`/api/work-items/${i}/documents`)).json()).documents as { document_id: string }[], id);
    await ph(c, item(c, "needs-gate", `?doc=${docs[0]?.document_id ?? "missing"}`));
  }),
  cell("phone-doc", "missing", (c) => ph(c, item(c, "needs-gate", "?doc=does-not-exist"))),

  // G: new work item, and the sign-in card.
  cell("phone-new", "empty", (c) => ph(c, "/work-items/new")),
  cell("phone-new", "filled", (c) => ph(c, "/work-items/new", async (p) => {
    await p.getByRole("textbox", { name: "Title" }).fill("Design the caching layer for document search");
    await p.getByRole("textbox", { name: "Brief" }).fill("Cache embeddings by content hash; invalidate on reindex.");
  })),
  cell("phone-new", "attach-sheet", (c) => ph(c, "/work-items/new", click("+ spec"))),
  cell("phone-new", "discard", (c) => ph(c, "/work-items/new", async (p) => { await p.getByRole("textbox", { name: "Title" }).fill("keep me"); await p.getByRole("button", { name: "Cancel" }).click(); })),
  ...(["idle", "wrong", "locked"] as const).map((v) => cell("phone-login", v, async (c) => {
    await c.page.goto("/");
    await c.page.getByLabel("Password").waitFor({ timeout: 8000 });
    if (v !== "idle") { await c.page.getByLabel("Password").fill("pw"); await c.page.getByRole("button", { name: "Sign in" }).click(); }
    await settle(c.page, 700);
  }, { locked: true, login: v === "idle" ? "ok" : v })),

  // I: Search.
  cell("phone-search", "default", (c) => ph(c, "/search")),
  cell("phone-search", "results", (c) => ph(c, "/search?q=cache")),
  cell("phone-search", "none", (c) => ph(c, "/search?q=zzzzqx")),
  cell("phone-search", "filter-sheet", (c) => ph(c, "/search?q=cache", async (p) => { await p.getByRole("button", { name: /^Kind/ }).first().click(); })),

  // J: Analytics.
  cell("phone-analytics", "default", (c) => ph(c, "/analytics")),
  cell("phone-analytics", "long", (c) => ph(c, "/analytics"), { data: "long" }),
  cell("phone-analytics", "empty", (c) => ph(c, "/analytics"), { data: "empty" }),
  cell("phone-analytics", "range-sheet", (c) => ph(c, "/analytics", click(/^(7|14|30) ?d|Last|Range/))),

  // K: More, with and without work waiting to apply.
  cell("phone-more", "default", (c) => ph(c, "/more"), { mock: { apply: "none", areas: "default" } }),
  cell("phone-more", "drafts", (c) => ph(c, "/more"), { mock: { apply: "none", areas: "broken" } }),
  cell("phone-more", "reload", (c) => ph(c, "/more"), { mock: { apply: "reload" } }),
  cell("phone-more", "restart", (c) => ph(c, "/more"), { mock: { apply: "restart" } }),
  cell("phone-more", "restart-confirm", (c) => ph(c, "/more", click("Restart Kraft")), { mock: { apply: "restart" } }),
  cell("phone-more", "unmanaged", (c) => ph(c, "/more"), { mock: { apply: "unmanaged" } }),

  // L: Chains and Library.
  cell("phone-area", "chains-list", (c) => ph(c, "/templates/chains")),
  cell("phone-area", "chain", (c) => ph(c, "/templates/chains/default")),
  cell("phone-area", "chain-node", (c) => ph(c, "/templates/chains/default/nodes/verification")),
  cell("phone-area", "chain-problem", (c) => ph(c, "/templates/chains/broken")),
  cell("phone-area", "chain-yaml", (c) => ph(c, "/templates/chains/default?yaml=1")),
  cell("phone-area", "library-list", (c) => ph(c, "/templates/library"), { mock: { ngLibrary: "draft" } }),
  cell("phone-area", "library-steering", (c) => ph(c, "/templates/library?kind=steering"), { mock: { ngLibrary: "draft" } }),
  cell("phone-area", "library-component", (c) => ph(c, "/templates/library/tasks.implementer"), { mock: { ngLibrary: "draft" } }),

  // M: Harnesses and Repos.
  cell("phone-area", "harnesses-list", (c) => ph(c, "/templates/harnesses"), { mock: { harnesses: "floor" } }),
  cell("phone-area", "harnesses-problems", (c) => ph(c, "/templates/harnesses"), { mock: { harnesses: "problems" } }),
  cell("phone-area", "harness", (c) => ph(c, "/templates/harnesses/claude"), { mock: { harnesses: "floor" } }),
  cell("phone-area", "harness-access-sheet", (c) => ph(c, "/templates/harnesses/claude", click(/^Access/)), { mock: { harnesses: "floor" } }),
  cell("phone-area", "profile", (c) => ph(c, "/templates/harnesses/profiles/strong"), { mock: { harnesses: "floor" } }),
  cell("phone-area", "repos-list", (c) => ph(c, "/templates/repos"), { mock: { areas: "default" } }),
  cell("phone-area", "repos-empty", (c) => ph(c, "/templates/repos"), { mock: { areas: "empty" } }),
  cell("phone-area", "repo", (c) => ph(c, "/templates/repos/platform"), { mock: { areas: "default" } }),
  cell("phone-area", "repo-problem", (c) => ph(c, "/templates/repos/docs-site"), { mock: { areas: "broken" } }),
  cell("phone-area", "repo-disconnect", (c) => ph(c, "/templates/repos/platform", click("Disconnect repo")), { mock: { areas: "default" } }),

  // N: Policy and Auto-intake.
  cell("phone-area", "policy-limits", (c) => ph(c, "/settings/policy/limits"), { mock: { areas: "default" } }),
  cell("phone-area", "policy-loops", (c) => ph(c, "/settings/policy/loops"), { mock: { areas: "default" } }),
  cell("phone-area", "policy-housekeeping", (c) => ph(c, "/settings/policy/housekeeping"), { mock: { areas: "default" } }),
  cell("phone-area", "policy-edit", (c) => ph(c, "/settings/policy/limits", async (p) => { await p.getByRole("button", { name: /^running/ }).first().click(); await p.getByRole("button", { name: /Edit default/ }).click(); }), { mock: { areas: "default" } }),
  cell("phone-area", "policy-problems", (c) => ph(c, "/settings/policy/limits"), { mock: { areas: "broken" } }),
  cell("phone-area", "policy-review", (c) => ph(c, "/settings/policy/housekeeping", async (p) => { await p.getByRole("button", { name: /^max active items/ }).click(); await p.locator("input[aria-label='max active items']").fill("7"); await p.keyboard.press("Enter"); await settle(p, 500); await p.getByRole("button", { name: "Review & publish" }).click(); }), { mock: { areas: "default" } }),
  cell("phone-area", "policy-stale", (c) => ph(c, "/settings/policy/housekeeping", async (p) => { await p.getByRole("button", { name: /^max active items/ }).click(); await p.locator("input[aria-label='max active items']").fill("7"); await p.keyboard.press("Enter"); await settle(p, 500); await p.getByRole("button", { name: "Review & publish" }).click(); await p.getByRole("button", { name: "Publish", exact: true }).click(); }), { mock: { areas: "stale" } }),
  cell("phone-area", "policy-yaml", (c) => ph(c, "/settings/policy/limits?yaml=1"), { mock: { areas: "default" } }),
  cell("phone-area", "intake-default", (c) => ph(c, "/settings/auto-intake"), { mock: { areas: "default" } }),
  cell("phone-area", "intake-off", (c) => ph(c, "/settings/auto-intake"), { mock: { areas: "off" } }),
  cell("phone-area", "intake-empty", (c) => ph(c, "/settings/auto-intake"), { mock: { areas: "empty" } }),
  cell("phone-area", "intake-schedule", (c) => ph(c, "/settings/auto-intake/schedules/0"), { mock: { areas: "default" } }),
  cell("phone-area", "intake-remove", (c) => ph(c, "/settings/auto-intake/schedules/0", click("Remove schedule")), { mock: { areas: "default" } }),
  cell("phone-area", "intake-checks", (c) => ph(c, "/settings/auto-intake", async (p) => { await p.getByRole("region", { name: "Recent checks" }).scrollIntoViewIfNeeded(); }), { mock: { areas: "default" } }),

  // O: Notifications, Access, Appearance, About. The browser's permission is stubbed: headless Chromium answers "denied" to everyone.
  ...([["list", "", "default", {}], ["webhook", "/webhook", "default", {}], ["webhook-config", "/webhook?tab=config", "default", {}], ["browser", "/browser", "default", {}], ["browser-config", "/browser?tab=config", "granted", {}], ["denied", "/browser?tab=config", "denied", {}], ["test-result", "/webhook?tab=config", "default", { last_test: { at: new Date(Date.UTC(2026, 8, 13, 9, 50)).toISOString(), status: null, ms: null, error: "connection refused" } }], ["yaml", "/webhook?yaml=1", "default", {}], ["not-set-up", "/webhook?tab=config", "default", { enabled: false, url_set: false, events: [], base_url: null, last_test: null }]] as const).map((v) => cell("phone-area", `notifications-${v[0]}`, async (c) => {
    await c.page.addInitScript((p) => { (window as any).Notification = class { static permission = p; static requestPermission = async () => p; }; }, v[2]);
    Object.assign(c.S.settings.notify, v[3]);
    await ph(c, `/settings/notifications${v[1]}`);
  }, { mock: { apply: "none" } })),
  cell("phone-area", "notifications-webhook-url-sheet", async (c) => { await ph(c, "/settings/notifications/webhook?tab=config", click(/^webhook URL/)); }, { mock: { apply: "none" } }),
  ...([["default", {}, "none"], ["restart-pending", { port: 9100 }, "restart"], ["env-locked", { port: 9100 }, "none"], ["loopback", { bind: "127.0.0.1" }, "none"]] as const).map(([variant, access, apply]) => cell("phone-area", `access-${variant}`, async (c) => {
    const h = c.S.settings.health;
    Object.assign(c.S.settings.access, access);
    if (c.S.settings.access.bind !== "127.0.0.1") h.bind = c.S.settings.access.bind;
    await ph(c, "/settings/access");
  }, { mock: { apply } })),
  cell("phone-area", "access-hosts-sheet", async (c) => { await ph(c, "/settings/access", click(/^hosts/)); }, { mock: { apply: "none" } }),
  cell("phone-area", "access-revoke-sheet", async (c) => { await ph(c, "/settings/access", async (p) => { await p.locator(".ph-row", { hasText: /seen/ }).nth(1).click(); }); }, { mock: { apply: "none" } }),
  cell("phone-area", "access-restart-confirm", async (c) => { await ph(c, "/settings/access", click(/Restart Kraft/)); }, { mock: { apply: "restart" } }),
  cell("phone-area", "appearance-default", async (c) => { Object.assign(c.S.settings.theme, { surface: "slate", accent: "blue", colour_amount: "subtle" }); await ph(c, "/settings/appearance"); }),
  cell("phone-area", "appearance-mono", async (c) => { Object.assign(c.S.settings.theme, { surface: "ink", accent: "none", colour_amount: "mono" }); await ph(c, "/settings/appearance"); }),
  cell("phone-area", "appearance-yaml", async (c) => { Object.assign(c.S.settings.theme, { surface: "slate", accent: "blue" }); await ph(c, "/settings/appearance?yaml=1"); }),
  ...(["available", "current", "unknown"] as const).map((update) => cell("phone-area", update === "available" ? "about-default" : `about-${update === "current" ? "current" : "unknown"}`, (c) => ph(c, "/settings/about"), { mock: { update } })),
  cell("phone-shell", "not-found", (c) => ph(c, "/nowhere-at-all")),
];
