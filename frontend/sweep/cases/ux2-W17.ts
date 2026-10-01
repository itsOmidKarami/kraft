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
const item = (c: Ctx, sc: string, tail = "") => `/ng/work-items/${c.S.ng[sc]}${tail}`;
const board = { ngBoard: true } as const;

const cell = (screen: string, variant: string, run: (c: Ctx) => Promise<void>, extra: Partial<Case> = {}): Case => ({ screen, variant, data: "default", widths: W, noLight: true, run, ...extra });

export const cells: Case[] = [
  // B: the board.
  cell("ng-phone-board", "default", (c) => ph(c, "/ng/"), { mock: board }),
  cell("ng-phone-board", "needs", (c) => ph(c, "/ng/?f=needs"), { mock: board }),
  cell("ng-phone-board", "running", (c) => ph(c, "/ng/?f=running"), { mock: board }),
  cell("ng-phone-board", "done", (c) => ph(c, "/ng/?f=done"), { mock: board }),
  cell("ng-phone-board", "repo-sheet", (c) => ph(c, "/ng/", click(/All repos/)), { mock: board }),
  cell("ng-phone-board", "long", (c) => ph(c, "/ng/"), { data: "long", mock: board }),
  cell("ng-phone-board", "many", (c) => ph(c, "/ng/"), { data: "many", mock: board }),
  cell("ng-phone-board", "empty", (c) => ph(c, "/ng/"), { data: "empty", mock: { ngBoard: "empty" } }),
  cell("ng-phone-board", "offline", async (c) => {
    await c.page.route(/\/api\/work-items(\?|$)/, (r) => r.abort());
    await ph(c, "/ng/");
  }, { mock: board }),

  // C: the item screen, by state, then its sheets.
  ...(["gate:needs-gate", "running:running", "capped:capped", "question:needs-you", "escalated:escalated", "failed:failed", "waiting:waiting", "conflict:conflict", "mr-closed:mr-closed", "paused:paused", "done:done", "cancelled:cancelled", "archived:archived", "worker-lost:worker-lost"] as const).map((s) => {
    const [variant, sc] = s.split(":");
    return cell("ng-phone-item", variant, (c) => ph(c, item(c, sc)));
  }),
  cell("ng-phone-item", "long", (c) => ph(c, item(c, "running")), { data: "long" }),
  cell("ng-phone-item", "pause-sheet", (c) => ph(c, item(c, "running"), click("Pause"))),
  cell("ng-phone-item", "kebab", (c) => ph(c, item(c, "running"), click("More actions"))),
  // The ng "capped" item stopped at a cap, which has no raise (Kraft-x8qzu); a budget stop does, so this cell makes it one.
  cell("ng-phone-item", "raise-sheet", async (c) => {
    const b = (c.S.bundles as any)[c.S.ng.capped].item;
    b.stop = { ...b.stop, kind: "budget", reason: "The budget ran out." };
    b.budget_cap = { cap_usd: 10, source: "item", spent_usd: 10 };
    await ph(c, item(c, "capped"), click(/Raise budget/));
  }),
  cell("ng-phone-composer", "steer-running", (c) => ph(c, item(c, "running", "?compose=steer"))),
  cell("ng-phone-composer", "steer-paused", (c) => ph(c, item(c, "paused", "?compose=steer"))),
  cell("ng-phone-composer", "reject", (c) => ph(c, item(c, "needs-gate", "?compose=reject"))),
  cell("ng-phone-composer", "answer", (c) => ph(c, item(c, "needs-you", "?compose=answer"))),
  cell("ng-phone-composer", "escalate", (c) => ph(c, item(c, "running", "?compose=escalate"))),
  cell("ng-phone-composer", "cancel", (c) => ph(c, item(c, "running", "?compose=cancel"))),

  // D: the node screen.
  cell("ng-phone-node", "running", (c) => ph(c, item(c, "running", "/nodes/verification"))),
  cell("ng-phone-node", "paused", (c) => ph(c, item(c, "paused", "/nodes/verification"))),
  cell("ng-phone-node", "failed", (c) => ph(c, item(c, "failed", "/nodes/verification"))),
  cell("ng-phone-node", "done", (c) => ph(c, item(c, "running", "/nodes/plan"))),
  cell("ng-phone-node", "todo", (c) => ph(c, item(c, "running", "/nodes/release"))),
  cell("ng-phone-node", "gate-waiting", (c) => ph(c, item(c, "needs-gate", "/nodes/final_review"))),
  cell("ng-phone-node", "gate-done", (c) => ph(c, item(c, "running", "/nodes/spec_approval"))),
  cell("ng-phone-node", "log", (c) => ph(c, item(c, "running", "/nodes/verification?tab=log"))),
  cell("ng-phone-node", "config", (c) => ph(c, item(c, "running", "/nodes/verification?tab=config"))),
  cell("ng-phone-node", "long", (c) => ph(c, item(c, "running", "/nodes/verification")), { data: "long" }),

  // E: the task screen.
  cell("ng-phone-task", "overview", (c) => ph(c, item(c, "running", "/nodes/verification?sel=verification.review.code_review"))),
  cell("ng-phone-task", "log", (c) => ph(c, item(c, "running", "/nodes/verification?sel=verification.review.code_review&tab=log"))),
  cell("ng-phone-task", "config", (c) => ph(c, item(c, "running", "/nodes/verification?sel=verification.review.code_review&tab=config"))),
  cell("ng-phone-task", "first-attempt", (c) => ph(c, item(c, "running", "/nodes/verification?sel=verification.review.code_review&attempt=1"))),
  cell("ng-phone-task", "not-started", (c) => ph(c, item(c, "running", "/nodes/release?sel=release.ship.release"))),
  cell("ng-phone-task", "thread", (c) => ph(c, item(c, "escalated", "/nodes/implementation?sel=implementation.escalation.escalation"))),
  cell("ng-phone-task", "long", (c) => ph(c, item(c, "running", "/nodes/verification?sel=verification.review.code_review")), { data: "long" }),

  // F: the gate review and a document.
  cell("ng-phone-gate", "doc", (c) => ph(c, item(c, "needs-gate", "/review"))),
  cell("ng-phone-gate", "file-open", (c) => ph(c, item(c, "needs-gate", "/review"), async (p) => { if (await p.locator(".ph-file-row").count()) await p.locator(".ph-file-row").first().click(); })),
  cell("ng-phone-gate", "reject", (c) => ph(c, item(c, "needs-gate", "/review?compose=reject"))),
  cell("ng-phone-gate", "long", (c) => ph(c, item(c, "needs-gate", "/review")), { data: "long" }),
  cell("ng-phone-doc", "default", async (c) => {
    await c.page.goto("/ng/");
    const id = c.S.ng["needs-gate"];
    const docs = await c.page.evaluate(async (i) => (await (await fetch(`/api/work-items/${i}/documents`)).json()).documents as { document_id: string }[], id);
    await ph(c, item(c, "needs-gate", `?doc=${docs[0]?.document_id ?? "missing"}`));
  }),
  cell("ng-phone-doc", "missing", (c) => ph(c, item(c, "needs-gate", "?doc=does-not-exist"))),

  // G: new work item, and the sign-in card.
  cell("ng-phone-new", "empty", (c) => ph(c, "/ng/work-items/new")),
  cell("ng-phone-new", "filled", (c) => ph(c, "/ng/work-items/new", async (p) => {
    await p.getByRole("textbox", { name: "Title" }).fill("Design the caching layer for document search");
    await p.getByRole("textbox", { name: "Brief" }).fill("Cache embeddings by content hash; invalidate on reindex.");
  })),
  cell("ng-phone-new", "attach-sheet", (c) => ph(c, "/ng/work-items/new", click("+ spec"))),
  cell("ng-phone-new", "discard", (c) => ph(c, "/ng/work-items/new", async (p) => { await p.getByRole("textbox", { name: "Title" }).fill("keep me"); await p.getByRole("button", { name: "Cancel" }).click(); })),
  ...(["idle", "wrong", "locked"] as const).map((v) => cell("ng-phone-login", v, async (c) => {
    await c.page.goto("/ng");
    await c.page.getByLabel("Password").waitFor({ timeout: 8000 });
    if (v !== "idle") { await c.page.getByLabel("Password").fill("pw"); await c.page.getByRole("button", { name: "Sign in" }).click(); }
    await settle(c.page, 700);
  }, { locked: true, login: v === "idle" ? "ok" : v })),

  // A: a screen not built yet (PR 1): Search, Analytics and More name themselves and link to the shipped page.
  cell("ng-phone-shell", "soon", (c) => ph(c, "/ng/search")),
];
