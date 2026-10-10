import type { Page } from "@playwright/test";
import type { Scenario, Variant } from "./mocks/fixtures";
import { withCapLimit, withFixRoundOutcome, withProducer, withReviewer, withScopes, withStop, withTestResult } from "./mocks/fixtures";
import { app, editorsRoute, item, type AppOpts } from "./kit";

/**
 * Every screen the icon audit visits: an area × state, with the steps that reach it on the app's mocked API.
 * A state is a page, a pane, a menu, a card or a dialog open, a seeded condition (a stop's cause, a test
 * result), or the phone at 390px. The audit lists each icon-only control it finds there.
 */

export type Viewport = "default" | "phone";
export interface State { area: string; state: string; viewport?: Viewport; app: (p: Page) => Promise<unknown> }

const click = (name: string | RegExp, exact = true) => async (p: Page) => {
  await p.getByRole("button", { name, exact: typeof name === "string" ? exact : undefined }).first().click();
  await p.waitForTimeout(600);
};
const away = async (p: Page) => { await p.mouse.move(700, 420); await p.waitForTimeout(600); };
const hoverEdge = (x: number) => async (p: Page) => { await p.mouse.move(x, 420); await p.waitForTimeout(700); };

/** The app's sidebar toggle: one button, its state in aria-pressed. */
const appToggle = async (p: Page) => { await p.getByRole("button", { name: "Pin sidebar" }).click(); await p.waitForTimeout(400); };
const appLogTab = (sel: string, data: Variant = "default") => (p: Page) =>
  app(p, item("running", `/nodes/verification?sel=${sel}&tab=log`), { data, ready: (pg) => pg.getByLabel("Log lines").or(pg.getByText(/No log for this attempt|Not started\.|No log yet/)).first().waitFor({ timeout: 8000 }) });
const appGateDoc = (data: Variant = "default") => async (p: Page) => {
  await app(p, item("needs-gate", "?sel=final_review"), { data, routes: editorsRoute });
  await p.getByRole("button", { name: /^Read / }).first().click();
  await p.getByRole("dialog").waitFor();
  await p.waitForTimeout(600);
};
const appSearchDoc = async (p: Page) => {
  await app(p, "/settings/policy", { routes: editorsRoute });
  await p.keyboard.press("ControlOrMeta+k");
  await p.getByRole("combobox", { name: /search/i }).fill("cache");
  await p.getByRole("listbox").locator('[data-section="docs"] [role="option"]').first().click();
  await p.getByRole("dialog").waitFor();
  await p.waitForTimeout(700);
};
const appSearch = (q: string, then?: (p: Page) => Promise<unknown>) => async (p: Page) => {
  await app(p, "/");
  await p.keyboard.press("ControlOrMeta+k");
  if (q) await p.getByRole("combobox", { name: /search/i }).fill(q);
  await p.waitForTimeout(700);
  if (then) await then(p);
};
/** The item header's parts: the main button's menu, ⋮, the four cards. */
const appHeader = (sc: string, then: (p: Page) => Promise<unknown>) => async (p: Page) => { await app(p, item(sc)); await then(p); await p.waitForTimeout(500); };
/** The main button: the app's is .item-main; the prototype's is the header button holding ▾. */
const appMain = (p: Page) => p.locator(".item-main-action").first();
const appMenuRow = (label: RegExp) => async (p: Page) => { await appMain(p).hover(); await p.waitForTimeout(400); await p.getByRole("menuitem", { name: label }).click(); };
/** A page reached by its URL. */
const appArea = (url: string, o: AppOpts = {}): Pick<State, "app"> => ({ app: (p) => app(p, url, o) });
const appPhone = (url: string | ((S: Scenario) => string), o: AppOpts = {}) => (p: Page) => app(p, url, { noShell: true, ...o });

const appReview = (data: Variant = "default", tail = "") => (p: Page) =>
  app(p, item("needs-gate", `/review${tail}`), { data, ready: (pg) => pg.locator(".review-page").waitFor({ timeout: 8000 }) });
const appSettings = (url: string): Pick<State, "app"> => ({ app: (p) => app(p, url) });

const FILE = '[data-file="kraft/progress.py"]';
const num = (p: Page, side: string, n: number) => p.locator(FILE).getByRole("button", { name: `Pick ${side} line ${n}`, exact: true }).first();
/** Pick a range on the review page's one file and open the composer on it (the + shows on the end row). */
export async function pickRange(p: Page, a: [string, number], b: [string, number]) {
  await num(p, a[0], a[1]).click();
  await num(p, b[0], b[1]).click({ modifiers: a[0] === b[0] ? ["Shift"] : [] });
  await p.waitForTimeout(250);
}
const rangeOpen = (a: [string, number], b: [string, number], then?: (p: Page) => Promise<unknown>) => async (pg: Page) => {
  await pg.locator(".review-page").waitFor({ timeout: 8000 });
  const [x, y] = a[0] === b[0] ? [a, b] : [a, a];
  await pickRange(pg, x, y);
  if (a[0] !== b[0]) {
    const s = (await num(pg, a[0], a[1]).boundingBox())!, e = (await num(pg, b[0], b[1]).boundingBox())!;
    await pg.mouse.move(s.x + 4, s.y + s.height / 2); await pg.mouse.down(); await pg.mouse.move(e.x + 4, e.y + e.height / 2, { steps: 8 }); await pg.mouse.up();
  } else await num(pg, b[0], b[1]).hover();
  await pg.waitForTimeout(250);
  await pg.locator(".rv-plus:visible").first().click();
  await pg.getByRole("group", { name: /^Comment:/ }).first().waitFor();
  await pg.getByRole("textbox", { name: "Comment" }).first().fill("A note on this range");
  if (then) await then(pg);
  await pg.waitForTimeout(400);
};
const dragLastHandle = async (pg: Page) => {
  const g = (await pg.locator('[data-tip="Drag to change the last line"]').first().boundingBox())!, t = (await num(pg, "new", 8).boundingBox())!;
  await pg.mouse.move(g.x + g.width / 2, g.y + g.height / 2); await pg.mouse.down(); await pg.mouse.move(t.x + 4, t.y + t.height / 2, { steps: 6 }); await pg.mouse.up();
};
const reveal = async (pg: Page) => { await pg.mouse.move(3, 420); await pg.waitForTimeout(600); await pg.mouse.move(100, 420, { steps: 4 }); await pg.waitForTimeout(500); };

const at = (_name: string, url: ReturnType<typeof item> | string, o: AppOpts = {}): Pick<State, "app"> => ({ app: (p) => app(p, url, o) });



export const STATES: State[] = [
  // Sidebar
  { area: "sidebar", state: "expanded", app: (p) => app(p, "/") },
  { area: "sidebar", state: "collapsed", app: async (p) => { await app(p, "/"); await appToggle(p); await away(p); } },
  { area: "sidebar", state: "hover-expand", app: async (p) => { await app(p, "/"); await appToggle(p); await away(p); await hoverEdge(3)(p); } },
  { area: "sidebar", state: "pinned", app: async (p) => { await app(p, "/"); await appToggle(p); await away(p); await hoverEdge(3)(p); await appToggle(p); await away(p); } },
  { area: "sidebar", state: "update", app: (p) => app(p, "/") },

  // Board
  { area: "board", state: "default", app: (p) => app(p, "/") },
  { area: "board", state: "empty", app: (p) => app(p, "/", { data: "empty", mock: { ngBoard: "empty" } }) },
  { area: "board", state: "loading", app: (p) => app(p, "/", { mock: { boardState: "loading" }, ready: async () => {} }) },
  { area: "board", state: "error", app: (p) => app(p, "/", { mock: { boardState: "offline" }, ready: (pg) => pg.getByText(/offline/i).first().waitFor() }) },
  { area: "board", state: "peek", app: async (p) => { await app(p, "/"); await p.getByRole("button", { name: /^Design the caching layer/ }).first().click(); await p.waitForTimeout(700); } },

  // Work item
  { area: "work-item", state: "default", app: (p) => app(p, item("running")) },
  { area: "work-item", state: "node", app: (p) => app(p, item("running", "/nodes/verification")) },
  { area: "work-item", state: "node-earlier-pass", app: (p) => app(p, item("running", "/nodes/verification?pass=1"), { tweak: withScopes }) },
  { area: "work-item", state: "node-scope", app: (p) => app(p, item("running", `/nodes/verification?sel=verification.test.unit_tests&${new URLSearchParams({ scope: ":just test-unit" })}`), { tweak: withScopes }) },
  { area: "work-item", state: "loading", app: (p) => app(p, item("running"), { routes: (pg) => pg.route(/\/api\/work-items\/[0-9a-f]+(\?|$)/, () => {}), ready: async () => {} }) },
  { area: "work-item", state: "error", app: (p) => app(p, "/work-items/0000000000000000deadbeef", { ready: async () => {} }) },
  { area: "work-item", state: "failed", app: (p) => app(p, item("failed")) },
  { area: "work-item", state: "escalated", app: (p) => app(p, item("escalated")) },

  // Gates and review
  { area: "gates-review", state: "gate-chain", app: (p) => app(p, item("needs-gate")) },
  { area: "gates-review", state: "gate-view", app: (p) => app(p, item("needs-gate", "/nodes/final_review")) },
  { area: "gates-review", state: "gate-reject", app: async (p) => { await app(p, item("needs-gate", "?sel=final_review")); await click("Reject…")(p); } },
  { area: "gates-review", state: "gate-review", app: (p) => appReview("default", "?doc=1")(p) },
  { area: "gates-review", state: "review", app: (p) => appReview()(p) },

  // Document viewer
  { area: "doc-viewer", state: "default", app: appGateDoc() },
  { area: "doc-viewer", state: "fullscreen", app: async (p) => { await appGateDoc()(p); await click(/full screen/)(p); } },
  { area: "doc-viewer", state: "editor-menu", app: async (p) => { await appSearchDoc(p); await p.getByRole("dialog").getByRole("button", { name: "Other editors" }).click(); await p.waitForTimeout(400); } },
  { area: "doc-viewer", state: "from-search", app: appSearchDoc },

  // Logs viewer
  { area: "logs-viewer", state: "default", app: appLogTab("verification.review.code_review") },
  { area: "logs-viewer", state: "empty", app: appLogTab("verification.review.automated_review") },
  { area: "logs-viewer", state: "fullscreen", app: async (p) => { await appLogTab("verification.review.code_review")(p); await click(/full screen/)(p); } },

  // Settings
  { area: "settings", state: "policy", ...appSettings("/settings/policy") },
  { area: "settings", state: "auto-intake", ...appSettings("/settings/auto-intake") },
  { area: "settings", state: "notifications", ...appSettings("/settings/notifications") },
  { area: "settings", state: "access", ...appSettings("/settings/access") },
  { area: "settings", state: "appearance", ...appSettings("/settings/appearance") },
  { area: "settings", state: "about", ...appSettings("/settings/about") },
  // Item header (H1–H6): the main button's menu, ⋮, the four anchored cards, the diff line
  { area: "work-item", state: "header-menu", app: appHeader("running", async (p) => { await appMain(p).hover(); }) },
  { area: "work-item", state: "header-kebab", app: appHeader("running", (p) => p.getByRole("button", { name: "Item menu" }).click()) },
  { area: "work-item", state: "header-pause", app: appHeader("running", appMenuRow(/^Pause/)) },
  { area: "work-item", state: "header-escalate", app: appHeader("failed", appMenuRow(/^Escalate/)) },
  { area: "work-item", state: "header-complete", app: appHeader("running", appMenuRow(/^Mark complete/)) },
  { area: "work-item", state: "header-cancel", app: appHeader("running", appMenuRow(/^Cancel/)) },
  { area: "work-item", state: "header-title-edit", app: appHeader("running", (p) => p.locator(".item-title-btn").click()) },
  { area: "work-item", state: "needs-gate-header", app: (p) => app(p, item("needs-gate")) },

  // Search (Q1–Q5)
  { area: "search", state: "empty", app: appSearch("") },
  { area: "search", state: "results", app: appSearch("cache") },
  { area: "search", state: "filters", app: appSearch("cache", async (p) => { await p.getByRole("tab", { name: /Documents/ }).click().catch(() => {}); await p.getByRole("button", { name: /^Filters/ }).click(); }) },
  { area: "search", state: "no-match", app: appSearch("zzzqx") },

  // New item: the composer and the draft page (NI-)
  { area: "new-item", state: "composer", app: (p) => app(p, "/?new=1", { ready: (pg) => pg.getByText(/nodes run ·/).waitFor() }) },
  { area: "new-item", state: "composer-filled", app: async (p) => { await app(p, "/?new=1", { ready: (pg) => pg.getByText(/nodes run ·/).waitFor() }); await p.getByRole("textbox", { name: "Title" }).fill("Design the caching layer for document search"); await p.getByRole("textbox", { name: "Brief" }).fill("Cache embeddings by content hash; invalidate on reindex."); await p.waitForTimeout(600); } },
  { area: "new-item", state: "chain-menu", app: async (p) => { await app(p, "/?new=1", { ready: (pg) => pg.getByText(/nodes run ·/).waitFor() }); await p.getByRole("button", { name: /^default/ }).click(); await p.waitForTimeout(400); } },
  { area: "new-item", state: "draft-page", app: (p) => app(p, `/work-items/new?${new URLSearchParams({ repo: "/Users/dev/code/kraft-plugins", chain: "default", title: "Design the caching layer for document search" })}`) },
  { area: "new-item", state: "draft-node", app: async (p) => { await app(p, `/work-items/new?${new URLSearchParams({ repo: "/Users/dev/code/kraft-plugins", chain: "default", title: "Design the caching layer for document search" })}`, { ready: (pg) => pg.getByText(/nodes run ·/).first().waitFor() }); await p.waitForTimeout(500); { const n = p.getByRole("button", { name: /^verification,/ }).last(); await n.focus(); await p.keyboard.press("Enter"); } await p.waitForTimeout(600); } },

  // Chain / node graph (CG-): Templates › Chains, and the item's chain at the wide width
  { area: "chain-graph", state: "chains-canvas", ...appArea("/templates/chains/default", { ready: (pg) => pg.locator("main h1, .gcanvas, svg").first().waitFor() }) },
  { area: "chain-graph", state: "chains-node", app: (p) => app(p, "/templates/chains/default/nodes/verification") },
  { area: "chain-graph", state: "chains-pane", app: async (p) => { await app(p, "/templates/chains/default", { ready: (pg) => pg.locator(".canvas").first().waitFor() }); await p.waitForTimeout(700); { const n = p.getByRole("button", { name: /^verification,/ }).last(); await n.focus(); await p.keyboard.press("Enter"); } await p.waitForTimeout(600); } },
  { area: "chain-graph", state: "chains-yaml", app: async (p) => { await app(p, "/templates/chains/default"); await p.getByRole("tab", { name: "YAML" }).or(p.getByRole("button", { name: "YAML" })).first().click(); await p.waitForTimeout(600); } },
  { area: "chain-graph", state: "item-gate-node", app: (p) => app(p, item("needs-gate", "/nodes/final_review")) },
  { area: "chain-graph", state: "item-done", app: (p) => app(p, item("done")) },

  // Edit mode and Apply on a running item (ED-)
  { area: "edit-mode", state: "draft-chip", app: (p) => app(p, item("running"), { mock: { itemDraft: "changes" } }) },
  { area: "edit-mode", state: "config-override", app: async (p) => { await app(p, item("running", "?sel=work_brief.write.work_brief&tab=config"), { mock: { itemDraft: "none" } }); await p.getByRole("button", { name: "Override model" }).click(); await p.getByRole("textbox", { name: "model" }).fill("opus"); await p.keyboard.press("Enter"); await p.waitForTimeout(600); } },
  { area: "edit-mode", state: "apply", app: async (p) => { await app(p, item("running"), { mock: { itemDraft: "changes" } }); await click("Review & apply")(p); await p.getByRole("dialog").waitFor(); } },
  { area: "edit-mode", state: "problems", app: (p) => app(p, item("running"), { mock: { itemDraft: "problems" } }) },
  { area: "edit-mode", state: "seam-menu", app: async (p) => { await app(p, item("running"), { mock: { itemDraft: "none" } }); await p.getByRole("button", { name: "Add a library node here" }).first().focus(); await p.keyboard.press("Enter"); await p.waitForTimeout(500); } },

  // Templates (TP-)
  { area: "templates", state: "library", ...appArea("/templates/library", { mock: { ngLibrary: "draft" } }) },
  { area: "templates", state: "library-task", app: (p) => app(p, "/templates/library/tasks.implementer", { mock: { ngLibrary: "draft" } }) },
  { area: "templates", state: "harnesses", ...appArea("/settings/harnesses", { mock: { harnesses: "floor" } }) },
  { area: "templates", state: "harnesses-harness", app: (p) => app(p, "/settings/harnesses?harness=claude", { mock: { harnesses: "floor" } }) },
  { area: "templates", state: "repos", ...appArea("/settings/repos", { mock: { areas: "default" } }) },
  { area: "templates", state: "repos-repo", app: (p) => app(p, "/settings/repos/platform", { mock: { areas: "default" } }) },
  { area: "templates", state: "chains-list", ...appArea("/templates/chains") },

  // Analytics (AN-)
  { area: "analytics", state: "default", ...appArea("/analytics") },
  { area: "analytics", state: "empty", app: (p) => app(p, "/analytics", { data: "empty", mock: { ngBoard: "empty" } }) },

  // Sign-in and first run (SI-)
  { area: "sign-in", state: "idle", app: (p) => app(p, "/", { mock: { locked: true }, noShell: true, ready: (pg) => pg.getByLabel(/^Password/).waitFor() }) },
  { area: "sign-in", state: "error", app: async (p) => { await app(p, "/", { mock: { locked: true, login: "wrong" }, noShell: true, ready: (pg) => pg.getByLabel(/^Password/).waitFor() }); await p.getByLabel(/^Password/).fill("hunter2"); await p.keyboard.press("Enter"); await p.waitForTimeout(800); } },
  { area: "sign-in", state: "locked", app: async (p) => { await app(p, "/", { mock: { locked: true, login: "locked" }, noShell: true, ready: (pg) => pg.getByLabel(/^Password/).waitFor() }); await p.getByLabel(/^Password/).fill("hunter2"); await p.keyboard.press("Enter"); await p.waitForTimeout(800); } },
  { area: "sign-in", state: "first-run", app: (p) => app(p, "/", { data: "empty", mock: { ngBoard: "empty" } }) },
  { area: "sign-in", state: "first-run-probed", app: async (p) => { await app(p, "/", { data: "empty", mock: { ngBoard: "empty" } }); await p.getByLabel(/Path to a local git checkout/).fill("/Users/dev/code/acme"); await p.getByRole("button", { name: "+ Add repo" }).click(); await p.waitForTimeout(2500); } },

  // Phone, 390px (PH-): the app at 390 against the mobile prototype
  { area: "phone", state: "board", viewport: "phone", app: appPhone("/") },
  { area: "phone", state: "item-running", viewport: "phone", app: appPhone(item("running")) },
  { area: "phone", state: "item-gate", viewport: "phone", app: appPhone(item("needs-gate")) },
  { area: "phone", state: "item-capped", viewport: "phone", app: appPhone(item("capped")) },
  { area: "phone", state: "item-question", viewport: "phone", app: appPhone(item("needs-you")) },
  { area: "phone", state: "node", viewport: "phone", app: appPhone(item("running", "/nodes/verification")) },
  { area: "phone", state: "node-earlier-pass", viewport: "phone", app: appPhone(item("running", "/nodes/verification?pass=1"), { tweak: withScopes }) },
  { area: "phone", state: "scope", viewport: "phone", app: appPhone(item("running", `/nodes/verification?sel=verification.test.unit_tests&${new URLSearchParams({ scope: ":just test-unit" })}`), { tweak: withScopes }) },
  { area: "phone", state: "gate-node", viewport: "phone", app: appPhone(item("needs-gate", "/nodes/final_review")) },
  { area: "phone", state: "gate-review", viewport: "phone", app: appPhone(item("needs-gate", "/review"), { ready: (pg) => pg.locator("main").first().waitFor() }) },
  { area: "phone", state: "new-item", viewport: "phone", app: appPhone("/work-items/new") },
  { area: "phone", state: "search", viewport: "phone", app: appPhone("/search") },
  { area: "phone", state: "analytics", viewport: "phone", app: appPhone("/analytics") },
  { area: "phone", state: "more", viewport: "phone", app: appPhone("/more") },
  { area: "phone", state: "chains", viewport: "phone", app: appPhone("/templates/chains") },
  { area: "phone", state: "policy", viewport: "phone", app: appPhone("/settings/policy") },
  { area: "phone", state: "sign-in", viewport: "phone", app: (p) => app(p, "/", { mock: { locked: true }, noShell: true, ready: (pg) => pg.getByLabel(/^Password/).waitFor() }) },

  // Follow-ups: what a seed adds, review ranges, the revealed sidebar, the phone
  // Follow-ups merged since sweep 2
  { area: "follow-ups", state: "reviewer-pane", ...at("a gate's auto_review opens its own task pane", item("needs-gate", "/nodes/final_review?sel=final_review.auto_review"), { tweak: withReviewer }) },
  { area: "follow-ups", state: "reviewer-gate", ...at("the gate pane that owns the reviewer", item("needs-gate", "?sel=final_review"), { tweak: withReviewer }) },
  { area: "follow-ups", state: "recent-committed", ...at("Recent: the fix committed a change", item("running"), { tweak: withFixRoundOutcome(true) }) },
  { area: "follow-ups", state: "gate-tests-fail", ...at("gate pane: tests ✗ 1 of 3 scopes", item("needs-gate", "?sel=final_review"), { tweak: withTestResult(3, 1) }) },
  { area: "follow-ups", state: "failed-forge-auth", ...at("failed card, forge_auth", item("failed"), { tweak: withStop("infra", { cause: "forge_auth" }) }) },
  { area: "follow-ups", state: "capped-limit", ...at("capped item whose stop names its limit: header, banner, spent line", item("capped"), { tweak: withCapLimit }) },
  { area: "follow-ups", state: "capped-limit-peek", ...at("…and its board peek", "/", { tweak: withCapLimit, ready: async (pg) => { await pg.getByRole("button", { name: /^Fix flaky retry test/ }).first().click(); await pg.waitForTimeout(700); } }) },

  { area: "follow-ups", state: "gate-doc-by", ...at("a gate's document: written by, Open in editor", item("needs-gate", "?sel=final_review"), { tweak: withProducer, routes: editorsRoute, ready: async (pg) => { await pg.getByRole("button", { name: /^Read / }).first().click(); await pg.getByRole("dialog").waitFor(); } }) },
  { area: "follow-ups", state: "review-by", ...at("the gate review: written by", item("needs-gate", "/review?doc=1"), { tweak: withProducer, ready: (pg) => pg.locator(".review-page").waitFor({ timeout: 8000 }) }) },

  // Review ranges
  { area: "review-ranges", state: "range-new", ...at("composer on lines 2–7: header Lines 2–7", item("needs-gate", "/review"), { ready: rangeOpen(["new", 2], ["new", 7]) }) },
  { area: "review-ranges", state: "range-dragged", ...at("…after dragging the last handle one row down: Lines 2–8, the text kept", item("needs-gate", "/review"), { ready: rangeOpen(["new", 2], ["new", 7], dragLastHandle) }) },
  { area: "review-ranges", state: "range-mixed", ...at("across sides: Old 5 – new 6", item("needs-gate", "/review"), { ready: rangeOpen(["old", 5], ["new", 6]) }) },

  // Light mode
  { area: "follow-ups", state: "sidebar-revealed", ...at("unpinned, revealed: dark", "/", { side: "rail", ready: reveal }) },

  // Phone
  { area: "phone", state: "node-yaml", viewport: "phone", app: (p) => app(p, item("running", "/nodes/verification"), { noShell: true, ready: async (pg) => { await pg.getByRole("tab", { name: "YAML" }).click(); await pg.waitForTimeout(400); } }) },
  { area: "phone", state: "item-capped-limit", viewport: "phone", app: (p) => app(p, item("capped"), { noShell: true, tweak: withCapLimit }) },
  { area: "phone", state: "board-capped", viewport: "phone", app: (p) => app(p, "/", { noShell: true, ready: async (pg) => { await pg.getByRole("button", { name: /Raise (cap|budget)/ }).first().scrollIntoViewIfNeeded(); } }) },
];
