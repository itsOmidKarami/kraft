import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { buildScenario, type DisplayState, type Scenario } from "./fixtures";
import { installMocks, type MockOptions } from "./mockApi";
import { NG_NOW } from "./ngItems";
import { focusRingMissing } from "./checks";

/**
 * Interaction flows: each flow is a list of steps; every step runs an action,
 * waits, screenshots, and records what changed (URL, focused element, dialog
 * open, toast text). Videos are on for every flow (playwright config project
 * `interactions`). Nothing asserts except "the page did not crash".
 *
 * Output: e2e-shots/sweep/flow-<name>/<nn>-<step>@<width>.png + manifest lines
 * with screen "flow-<name>".
 */

const OUT = path.resolve("e2e-shots/sweep");
const MANIFEST = path.join(OUT, "manifest.jsonl");
const VP: Record<number, [number, number]> = { 390: [390, 844], 1280: [1280, 800] };

// `kbd` / `keyboard`: the step is driven from the keyboard, so the focus-ring
// check judges whatever holds focus, not only a :focus-visible element.
type Step = { name: string; run: (p: Page, S: Scenario) => Promise<void>; wait?: number; kbd?: true };
interface Flow { name: string; data?: "default" | "long"; state?: DisplayState; widths: number[]; start: (p: Page, S: Scenario) => Promise<void>; steps: Step[]; keyboard?: true; mock?: MockOptions }

const settle = (p: Page, ms = 400) => p.waitForTimeout(ms);
const item = (st: DisplayState) => async (p: Page, S: Scenario) => { await p.goto(`/work-items/${S.byState[st].item.id}`); await p.locator(".detail, .item-page, .phone-item").first().waitFor(); await settle(p, 600); };
// Inspector tabs and session rows live on the phone's node page (m05), not on its
// stage list (m04) — a phone flow about them has to start there.
const itemTabs = (st: DisplayState) => async (p: Page, S: Scenario) => {
  const it = S.byState[st].item;
  const hash = p.viewportSize()!.width < 768 && it.current_node_id ? `#node=${it.current_node_id}` : "";
  await p.goto(`/work-items/${it.id}${hash}`); await p.locator(".detail, .item-page, .phone-item").first().waitFor(); await settle(p, 600);
};
const board = async (p: Page) => { await p.goto("/"); await p.locator('[data-testid="board-card"]').first().waitFor(); await settle(p); };
const btn = (name: RegExp) => async (p: Page) => { await p.getByRole("button", { name }).first().click(); };
const key = (k: string, n = 1) => async (p: Page) => { for (let i = 0; i < n; i++) await p.keyboard.press(k); };
const NOTE = "Add an invalidation section for blob_sha changes mid-query.";

// UX V2 /ng shell flows. Assertions throw inside a step, which the manifest records as that step's error, so flow-completes fails on them.
const ng = (url: string) => async (p: Page) => { await p.goto(url); await p.locator("main h1").first().waitFor({ timeout: 8000 }); await settle(p, 600); };
// ux2-W5: the /ng item page for one of ngItems.ts's scenarios, the clock fixed.
const ngItem = (sc: string) => async (p: Page, S: Scenario) => { await p.clock.setFixedTime(new Date(NG_NOW)); await ng(`/ng/work-items/${S.ng[sc]}`)(p); };
// ux2-W6: the /ng board on its own fixtures (`mock: { ngBoard: true }`).
const ngBoard = (tail = "") => async (p: Page) => { await p.clock.setFixedTime(new Date(NG_NOW)); await ng(`/ng/${tail}`)(p); };
const sideWidth = async (p: Page) => (await p.locator(".ng-sidebar").boundingBox())!.width;
const sideIs = async (p: Page, mode: "pinned" | "rail") => {
  expect(await p.evaluate(() => document.documentElement.dataset.sidebar)).toBe(mode);
  // The width animates; poll until it settles.
  if (mode === "pinned") await expect.poll(() => sideWidth(p)).toBeGreaterThan(150); else await expect.poll(() => sideWidth(p)).toBeLessThan(100);
};
const reloadNg = async (p: Page) => { await p.reload(); await p.locator("main h1").first().waitFor({ timeout: 8000 }); await settle(p, 500); };
let focusBefore = "";
const activeId = (p: Page) => p.evaluate(() => { const a = document.activeElement as HTMLElement; return a ? `${a.tagName}#${a.id}.${a.className}` : ""; });
const searchBox = (p: Page) => p.getByRole("combobox", { name: "Search" });
// W10: the Chains editor on the mock's real draft answers.
const chains = (key: string) => async (p: Page) => { await p.addInitScript(() => localStorage.setItem("kraft.sidebar.v2", "pinned")); await p.goto(`/ng/templates/chains/${key}`); await p.locator(".canvas").first().waitFor({ timeout: 8000 }); await settle(p, 700); };

const FLOWS: Flow[] = [
  { name: "peek-open-close", widths: [1280, 390], start: board, steps: [
    { name: "click-row", run: async (p) => { const row = p.locator('[data-testid="board-card"]').first(); if (p.viewportSize()!.width < 768) { const b = (await row.boundingBox())!; await p.mouse.move(b.x + 40, b.y + 20); await p.mouse.down(); await p.waitForTimeout(650); await p.mouse.up(); } else await row.click(); } },
    { name: "peek-scroll-bottom", run: async (p) => { await p.getByLabel("peek").evaluate((el) => { el.scrollTop = el.scrollHeight; }); } },
    { name: "esc-closes", run: key("Escape") },
    { name: "second-row", run: async (p) => { await p.locator('[data-testid="board-card"]').nth(1).click(); } },
    { name: "title-click-navigates", run: async (p) => { await p.getByLabel("peek").getByRole("link", { name: /open/i }).first().click().catch(() => {}); } },
    { name: "browser-back", run: async (p) => { await p.goBack(); } },
  ] },
  // W11 · B.5: Approve from the board row acts without opening the peek; the row moves group.
  { name: "board-approve", widths: [1280], start: board, steps: [
    { name: "approve-on-row", run: async (p, S) => { const row = p.locator(`[data-testid="board-card"]:has-text("${S.byState.gate.item.title.slice(0, 24)}")`).first(); await row.getByRole("button", { name: /^Approve$/ }).click(); }, wait: 900 },
    { name: "row-moved", run: async () => {}, wait: 600 },
  ] },
  { name: "gate-approve", state: "gate", widths: [1280, 390], start: item("gate"), steps: [
    // The control is an <a class="btn">, not a button.
    { name: "read-document", run: async (p) => { await p.getByRole("link", { name: /read document/i }).or(p.getByRole("button", { name: /read document/i })).first().click(); } },
    { name: "approve", run: btn(/^Approve$/) , wait: 900 },
    { name: "toast-visible", run: async () => {}, wait: 100 },
    { name: "after-3s", run: async () => {}, wait: 3000 },
  ] },
  { name: "gate-reject", state: "gate", widths: [1280, 390], start: item("gate"), steps: [
    { name: "open-reject", run: btn(/^Reject$/) },
    { name: "focus-is-textarea", run: async () => {}, kbd: true },
    { name: "type", run: async (p) => { await p.keyboard.type(NOTE); } },
    { name: "cmd-enter", run: async (p) => { await p.keyboard.press("Meta+Enter"); }, wait: 800 },
    { name: "cancel-path", run: async (p) => { await p.getByRole("button", { name: /^Reject$/ }).first().click().catch(() => {}); await p.getByRole("button", { name: /cancel/i }).first().click().catch(() => {}); } },
  ] },
  { name: "pause-steer-resume", state: "running", widths: [1280, 390], start: item("running"), steps: [
    { name: "pause", run: btn(/^Pause$/), wait: 800 },
    // Steer the item this flow just paused (the mock now mutates on pause), not a different paused item.
    { name: "steer-open", run: async (p) => { await p.getByRole("button", { name: /^Steer$/ }).first().click(); } },
    { name: "type", run: async (p) => { await p.keyboard.type(NOTE); } },
    { name: "submit", run: btn(/resume with this steer/i), wait: 800 },
  ] },
  // Kraft-dkb6g: `data: "long"` so this item has two escalation threads --
  // the caret/new-thread/timeline steps below need a prior thread to act
  // on. Existing steps' indices (00-02) are kept as they were; the new
  // steps append after them rather than renumbering (the spec's own literal
  // 02/03/04 numbering assumed `dismiss` was dropped or moved -- it isn't,
  // so this flow's steps land at 03-05 instead. Flagging here rather than
  // silently matching the spec text, per its own instruction).
  { name: "escalate-thread", state: "escalated", data: "long", widths: [1280, 390], start: item("escalated"), steps: [
    { name: "reply-open", run: btn(/^Reply/) },
    { name: "thread-scroll", run: async (p) => { await p.locator('[data-testid="escalation-thread"]').evaluate((el) => { el.scrollTop = el.scrollHeight; }).catch(() => {}); } },
    { name: "dismiss", run: async (p) => { await p.getByRole("button", { name: /cancel/i }).first().click().catch(() => {}); await p.getByRole("button", { name: /dismiss/i }).first().click().catch(() => {}); }, wait: 600 },
    { name: "caret-open", run: async (p) => { await p.getByRole("button", { name: /^Reply$/ }).first().click().catch(() => {}); await p.getByRole("button", { name: /reply options/i }).click().catch(() => {}); } },
    { name: "new-thread-submit", run: async (p) => { await p.getByRole("menuitem", { name: /reply in new thread/i }).click().catch(() => {}); }, wait: 800 },
    { name: "timeline-two-threads", run: async (p) => { await p.getByRole("tab", { name: /timeline/i }).click().catch(() => {}); await p.getByTestId("timeline-escalation-thread-2").click().catch(() => {}); } },
  ] },
  { name: "item-tabs-keyboard", state: "gate", data: "long", widths: [1280], keyboard: true, start: item("gate"), steps: [
    { name: "tab-x5", run: key("Tab", 5) },
    { name: "tab-x10", run: key("Tab", 10) },
    { name: "tab-x20", run: key("Tab", 20) },
    { name: "arrow-right-in-tablist", run: async (p) => { await p.getByRole("tab").first().focus(); await p.keyboard.press("ArrowRight"); } },
    { name: "enter", run: key("Enter") },
  ] },
  { name: "inspector-tabs-selection", state: "gate", data: "long", widths: [1280, 390], start: itemTabs("gate"), steps: [
    { name: "documents", run: async (p) => { await p.getByRole("tab", { name: /documents/i }).click(); } },
    { name: "pick-3rd-doc", run: async (p) => { await p.locator('[data-testid="inspector-documents"] li, [data-testid="inspector-documents"] button').nth(2).click(); } },
    { name: "changes", run: async (p) => { await p.getByRole("tab", { name: /changes/i }).click(); } },
    { name: "pick-file", run: async (p) => { await p.locator('.tree-row[data-kind="file"]').nth(3).click(); } },
    { name: "back-to-documents-restores", run: async (p) => { await p.getByRole("tab", { name: /documents/i }).click(); } },
    { name: "reload-restores", run: async (p) => { await p.reload(); await settle(p, 800); } },
    { name: "browser-back-leaves-page", run: async (p) => { await p.goBack(); await settle(p, 600); } },
  ] },
  { name: "log-maximize", state: "running", data: "long", widths: [1280, 390], start: itemTabs("running"), steps: [
    { name: "pick-session", run: async (p) => { await p.locator('[data-testid^="task-row-"]').first().click(); } },
    { name: "maximize", run: async (p) => { await p.getByRole("button", { name: /maximize/i }).first().click(); } },
    { name: "wheel-up-pauses-follow", run: async (p) => { await p.mouse.move(640, 400); await p.mouse.wheel(0, -2000); } },
    { name: "filter-agent", run: async (p) => { await p.locator(".log-chip", { hasText: /^agent$/ }).first().click().catch(() => {}); } },
    { name: "esc-restores", run: key("Escape") },
    { name: "back-after-maximize", run: async (p) => { await p.getByRole("button", { name: /maximize/i }).first().click(); await settle(p); await p.goBack(); await settle(p, 500); } },
  ] },
  { name: "graph-resize", state: "gate", data: "long", widths: [1280], start: item("gate"), steps: [
    { name: "drag-handle-down", run: async (p) => { const h = p.getByLabel(/resize the chain graph/i); const b = (await h.boundingBox())!; await p.mouse.move(b.x + b.width / 2, b.y + b.height / 2); await p.mouse.down(); await p.mouse.move(b.x + b.width / 2, b.y + 200, { steps: 8 }); await p.mouse.up(); } },
    { name: "drag-handle-up", run: async (p) => { const h = p.getByLabel(/resize the chain graph/i); const b = (await h.boundingBox())!; await p.mouse.move(b.x + b.width / 2, b.y + b.height / 2); await p.mouse.down(); await p.mouse.move(b.x + b.width / 2, b.y - 400, { steps: 8 }); await p.mouse.up(); } },
  ] },
  { name: "search-keyboard", data: "long", widths: [1280, 390], keyboard: true, start: board, steps: [
    { name: "cmd-k", run: key("Meta+k") },
    { name: "type", run: async (p) => { await p.keyboard.type("measured"); }, wait: 800 },
    { name: "arrow-down-x2", run: key("ArrowDown", 2) },
    { name: "enter-opens", run: key("Enter"), wait: 800 },
    { name: "esc", run: key("Escape") },
    { name: "back", run: async (p) => { await p.goBack(); await settle(p, 500); } },
  ] },
  { name: "new-item-full", data: "long", widths: [1280, 390], start: board, steps: [
    { name: "open", run: btn(/new work item/i) },
    { name: "title", run: async (p) => { await p.getByRole("dialog").getByLabel("title").fill("Design the caching layer for document search"); } },
    { name: "repo", run: async (p) => { const d = p.getByRole("dialog"); await d.getByLabel("repo").selectOption({ index: 1 }).catch(async () => { await d.getByRole("radio").first().click(); }); } },
    { name: "template-long", run: async (p) => { await p.getByRole("dialog").getByRole("radiogroup", { name: "template" }).getByRole("radio").nth(2).click(); } },
    { name: "skip-a-node", run: async (p) => { await p.getByRole("dialog").getByText(/^verify$/).first().click().catch(() => {}); } },
    // Phone keeps the overrides behind a disclosure (W3.8): open it, or the fill lands in a hidden field (Kraft-ow8wo).
    { name: "budget", run: async (p) => { const d = p.getByRole("dialog"); const t = d.getByRole("button", { name: /advanced · overrides/i }); if ((await t.isVisible()) && (await t.getAttribute("aria-expanded")) !== "true") await t.click(); await d.getByLabel("budget").fill("12.5"); } },
    { name: "scroll-bottom", run: async (p) => { await p.getByRole("dialog").evaluate((el) => { const s = el.querySelector("form, .modal-body, [class*=body]") ?? el; (s as HTMLElement).scrollTop = 99999; }); } },
    { name: "create-paused", run: btn(/create paused/i), wait: 900 },
  ] },
  { name: "board-selection-archive", widths: [1280, 390], start: board, steps: [
    { name: "select-all-done", run: async (p) => { await p.getByText(/select all/i).first().click(); } },
    { name: "selection-bar", run: async () => {} },
    { name: "archive", run: async (p) => { await p.getByTestId("archive-selected").click(); }, wait: 900 },
    // The Archived chip may sit under the facet bar's "+N" (W4.1); open it first when it does.
    { name: "archived-chip", run: async (p) => { const vis = () => p.locator('a[href="/archived"]:visible'); if (!(await vis().count())) await p.locator(".board-more > summary").click(); await vis().first().click(); }, wait: 700 },
  ] },
  { name: "phone-node-page", data: "long", widths: [390], start: item("gate"), steps: [
    { name: "tap-stage", run: async (p) => { await p.locator('[data-testid^="phone-stage-"]').nth(2).click(); } },
    { name: "swipe-tab", run: async (p) => { await p.mouse.move(300, 500); await p.mouse.down(); await p.mouse.move(60, 500, { steps: 10 }); await p.mouse.up(); } },
    { name: "back", run: async (p) => { await p.goBack(); await settle(p, 500); } },
    { name: "long-press-repo", run: async (p) => { await p.goto("/"); await settle(p, 600); const chip = p.locator(".board-facet button, .chip").first(); const b = (await chip.boundingBox())!; await p.mouse.move(b.x + 10, b.y + 10); await p.mouse.down(); await p.waitForTimeout(650); await p.mouse.up(); } },
  ] },
  { name: "settings-save-roundtrip", data: "long", widths: [1280, 390], start: async (p) => { await p.goto("/settings/policy"); await settle(p, 700); }, steps: [
    { name: "edit-field", run: async (p) => { const f = p.locator("main input[type=number]").first(); await f.fill("7"); } },
    { name: "dirty-state", run: async () => {} },
    { name: "save", run: btn(/^Save/i), wait: 900 },
    // W11 · D: no templates column to click "default" in; open the editor card on a node.
    { name: "chains-editor", run: async (p) => { await p.goto("/settings/chains"); await settle(p, 600); await p.locator(".chain-pill", { hasText: /^verify/ }).first().click(); await settle(p, 500); } },
    { name: "yaml-toggle", run: async (p) => { await p.getByRole("button", { name: /yaml/i }).first().click().catch(() => {}); } },
    { name: "add-node", run: async (p) => { await p.getByRole("button", { name: /add node/i }).first().click().catch(() => {}); } },
  ] },
  { name: "ng-search-keyboard", widths: [1280], start: ng("/ng/settings/policy"), steps: [
    { name: "open", run: async (p) => { focusBefore = await activeId(p); await p.keyboard.press("Control+k"); await expect(searchBox(p)).toBeVisible(); await expect(searchBox(p)).toBeFocused(); }, kbd: true },
    { name: "type", run: async (p) => { await searchBox(p).fill("gate"); await p.waitForTimeout(700); await expect(p.getByRole("option").first()).toBeVisible(); }, kbd: true, wait: 200 },
    { name: "down-down-up", run: async (p) => {
      const at = () => searchBox(p).getAttribute("aria-activedescendant");
      const start = await at();
      await p.keyboard.press("ArrowDown"); const one = await at();
      await p.keyboard.press("ArrowDown"); const two = await at();
      await p.keyboard.press("ArrowUp"); const back = await at();
      expect(new Set([start, one, two]).size).toBe(3);
      expect(back).toBe(one);
      await expect(searchBox(p)).toBeFocused();
    }, kbd: true },
    { name: "escape-returns-focus", run: async (p) => { await p.keyboard.press("Escape"); await expect(searchBox(p)).toHaveCount(0); expect(await activeId(p)).toBe(focusBefore); }, kbd: true },
    { name: "reopen-enter-goes", run: async (p) => {
      const url = p.url();
      await p.keyboard.press("Control+k"); await searchBox(p).fill("analytics"); await p.waitForTimeout(500);
      await p.keyboard.press("Enter");
      await expect(searchBox(p)).toHaveCount(0);
      await expect.poll(() => p.url()).not.toBe(url);
    }, kbd: true, wait: 700 },
  ] },
  { name: "ng-sidebar-pin", widths: [1280], start: ng("/ng/settings/policy"), steps: [
    { name: "default-pinned", run: async (p) => { await sideIs(p, "pinned"); } },
    { name: "unpin-with-shortcut", run: async (p) => { await p.keyboard.press("Control+\\"); await sideIs(p, "rail"); }, kbd: true },
    { name: "reload-stays-rail", run: async (p) => { await reloadNg(p); await sideIs(p, "rail"); } },
    { name: "pin-with-shortcut", run: async (p) => { await p.keyboard.press("Control+\\"); await sideIs(p, "pinned"); }, kbd: true },
    { name: "reload-stays-pinned", run: async (p) => { await reloadNg(p); await sideIs(p, "pinned"); } },
  ] },
  { name: "ng-sidebar-rail", widths: [1024], start: ng("/ng/settings/policy"), steps: [
    { name: "rail-by-default", run: async (p) => { await sideIs(p, "rail"); } },
    { name: "hover-reveals", run: async (p) => { await p.mouse.move(20, 300); await expect.poll(() => sideWidth(p)).toBeGreaterThan(150); } },
    { name: "leaving-collapses", run: async (p) => { await p.mouse.move(700, 450); await expect.poll(() => sideWidth(p)).toBeLessThan(100); } },
    { name: "focus-reveals", run: async (p) => { await p.getByRole("button", { name: "Search" }).first().focus(); await expect.poll(() => sideWidth(p)).toBeGreaterThan(150); }, kbd: true },
  ] },
  // ux2-W16 A: restart is explicit, behind a confirm, and the page waits for the server to come back.
  { name: "ng-apply-restart", widths: [1280], keyboard: true, mock: { apply: "restart" }, start: ng("/ng/settings/appearance"), steps: [
    { name: "chip-names-the-restart", run: async (p) => { await expect(p.getByRole("button", { name: "Restart needed, 1" })).toBeVisible(); } },
    { name: "popover-offers-restart", run: async (p) => { await p.getByRole("button", { name: "Restart needed, 1" }).focus(); await p.keyboard.press("Enter"); await expect(p.getByRole("button", { name: "Restart Kraft" })).toBeVisible(); }, kbd: true },
    { name: "confirm-first", run: async (p) => {
      await p.getByRole("button", { name: "Restart Kraft" }).click(); await expect(p.getByRole("dialog", { name: "Restart Kraft?" })).toBeVisible();
      expect(await p.evaluate(() => performance.getEntriesByType("resource").filter((e) => e.name.includes("/api/apply/restart")).length)).toBe(0);
    } },
    { name: "restarting", run: async (p) => { await p.getByRole("button", { name: "Restart", exact: true }).click(); await expect(p.getByRole("dialog", { name: "Restarting Kraft…" })).toBeVisible(); } },
    { name: "back-and-cleared", run: async (p) => { await expect(p.getByRole("dialog")).toHaveCount(0, { timeout: 15000 }); await expect(p.getByRole("button", { name: /Restart needed/ })).toHaveCount(0); } },
  ] },
  // ux2-W5 B.9: Cancel… reached by keyboard only, and the request is /cancel (never the route that deletes the worktree), R17.
  { name: "ng-cancel", widths: [1280], keyboard: true, start: ngItem("running"), steps: [
    { name: "toggle-focus-opens-panel", run: async (p) => { await p.getByRole("button", { name: "More actions" }).focus(); await expect(p.getByRole("menu", { name: "Item actions" })).toBeVisible(); } },
    { name: "arrows-to-cancel", run: async (p) => { await p.keyboard.press("ArrowDown"); await p.keyboard.press("ArrowDown"); await p.keyboard.press("ArrowDown"); await expect(p.getByRole("menuitem", { name: /Cancel/ })).toBeFocused(); } },
    { name: "enter-opens-card", run: async (p) => { await p.keyboard.press("Enter"); await expect(p.getByRole("dialog", { name: "Cancel this item?" })).toBeVisible(); await p.getByText(/stays on the ledger/).waitFor(); } },
    { name: "reason", run: async (p) => { await expect(p.getByLabel("Reason")).toBeFocused(); await p.keyboard.type("Superseded by kraft-cb61."); } },
    { name: "cancel-item", run: async (p) => {
      const sent = p.waitForRequest((r) => r.method() === "POST" && /\/work-items\/[^/]+\/(cancel|abandon)$/.test(r.url()));
      await p.keyboard.press("Tab"); await p.keyboard.press("Enter");
      const r = await sent;
      expect(new URL(r.url()).pathname).toMatch(/\/cancel$/);
      expect(r.postDataJSON()).toEqual({ reason: "Superseded by kraft-cb61.", close_mr: false });
      await expect(p.getByText("CANCELLED", { exact: true })).toBeVisible();
    } },
  ] },
  // ux2-W5 E: the pane by keyboard; the collapse a person chose survives picking nodes and moving to another item.
  { name: "ng-pane-collapse", widths: [1280], start: ngItem("running"), steps: [
    { name: "collapse", run: async (p) => { await p.getByRole("button", { name: "Collapse pane" }).focus(); await p.keyboard.press("Enter"); await expect(p.getByRole("button", { name: "Expand pane" })).toBeFocused(); }, kbd: true },
    // A click picks without opening (Enter opens, R6).
    { name: "pick-node-stays-collapsed", run: async (p) => { await p.getByRole("button", { name: /^implementation, node/ }).click(); await expect(p.getByRole("complementary", { name: "implementation pane, collapsed" })).toBeVisible(); } },
    { name: "other-item-stays-collapsed", run: async (p, S) => { await p.evaluate((url) => { history.pushState({}, "", url); dispatchEvent(new PopStateEvent("popstate")); }, `/ng/work-items/${S.ng.failed}`); await expect(p.getByText("FAILED", { exact: true })).toBeVisible(); await expect(p.getByRole("button", { name: "Expand pane" })).toBeVisible(); } },
    { name: "rail-expands", run: async (p) => { await p.getByRole("button", { name: "Expand pane" }).focus(); await p.keyboard.press("Enter"); await expect(p.getByRole("button", { name: "Collapse pane" })).toBeVisible(); }, kbd: true },
    { name: "escape-collapses", run: async (p) => { await p.getByRole("tab", { name: "Overview" }).focus(); await p.keyboard.press("Escape"); await expect(p.getByRole("button", { name: "Expand pane" })).toBeFocused(); }, kbd: true },
  ] },
  // ux2-W6 D.4 (R3): check two rows by keyboard, Cancel… with a reason, and the one bulk request goes only after the window.
  { name: "ng-board-select-bulk", widths: [1280], keyboard: true, mock: { ngBoard: true }, start: ngBoard(), steps: [
    { name: "space-checks-a-row", run: async (p) => { await p.getByRole("button", { name: /^Bump the VS Code/ }).focus(); await p.keyboard.press("Space"); await expect(p.getByRole("checkbox", { name: /^Select Bump the VS Code/ })).toBeChecked(); await expect(p.getByText("1 selected")).toBeVisible(); } },
    { name: "arrow-and-space-checks-another", run: async (p) => { await p.keyboard.press("ArrowDown"); await p.keyboard.press("Space"); await expect(p.getByText("2 selected")).toBeVisible(); } },
    { name: "cancel-asks", run: async (p) => { await p.getByRole("button", { name: "Cancel 2…" }).focus(); await p.keyboard.press("Enter"); await expect(p.getByRole("textbox", { name: /Reason/ })).toBeFocused(); await expect(p.getByRole("button", { name: "Cancel 2", exact: true })).toBeDisabled(); } },
    { name: "reason", run: async (p) => { await p.keyboard.type("Superseded by kraft-cb61"); } },
    { name: "confirm-holds-the-send", run: async (p) => {
      const sent: { at: number; body: unknown }[] = [];
      p.on("request", (r) => { if (r.method() === "POST" && /\/work-items\/(bulk|[^/]+\/(cancel|abandon))$/.test(r.url())) sent.push({ at: Date.now(), body: r.postDataJSON() }); });
      const t0 = Date.now();
      await p.keyboard.press("Tab"); await p.keyboard.press("Enter");
      await expect(p.getByText("Cancelling 2 items…")).toBeVisible();
      await p.waitForTimeout(4000);
      expect(sent).toEqual([]);
      await expect.poll(() => sent.length, { timeout: 4000 }).toBe(1);
      expect(sent[0].at - t0).toBeGreaterThanOrEqual(4900);
      expect(sent[0].body).toEqual({ action: "cancel", ids: expect.any(Array), reason: "Superseded by kraft-cb61" });
      expect((sent[0].body as { ids: string[] }).ids).toHaveLength(2);
    }, wait: 600 },
  ] },
  // ux2-W6 E: the peek by keyboard, its width kept across a reload, and ⌘/Ctrl-Enter to the item page.
  { name: "ng-peek-open", widths: [1280], keyboard: true, mock: { ngBoard: true }, start: ngBoard(), steps: [
    { name: "arrow-to-a-row", run: async (p) => { await p.getByRole("button", { name: /^Design the caching layer/ }).focus(); await p.keyboard.press("ArrowDown"); await expect(p.getByRole("button", { name: /^Fix flaky retry test/ })).toBeFocused(); } },
    { name: "enter-opens-the-peek", run: async (p) => { await p.keyboard.press("Enter"); await expect(p.getByRole("complementary", { name: "kraft-7d21 pane" })).toBeVisible(); await expect(p).toHaveURL(/[?&]sel=/); } },
    { name: "keys-widen-it", run: async (p) => {
      const h = p.getByRole("separator", { name: "Resize pane" });
      await h.focus();
      const w0 = Number(await h.getAttribute("aria-valuenow"));
      for (let i = 0; i < 4; i++) await p.keyboard.press("ArrowLeft");
      await expect(h).toHaveAttribute("aria-valuenow", String(w0 + 64));
    } },
    { name: "reload-keeps-the-width", run: async (p) => {
      const w = await p.getByRole("separator", { name: "Resize pane" }).getAttribute("aria-valuenow");
      await p.reload(); await p.locator("main h1").first().waitFor();
      await expect(p.getByRole("separator", { name: "Resize pane" })).toHaveAttribute("aria-valuenow", w!);
    }, wait: 700 },
    { name: "ctrl-enter-opens-the-item", run: async (p) => { await p.getByRole("button", { name: /^Fix flaky retry test/ }).focus(); await p.keyboard.press("Control+Enter"); await expect(p).toHaveURL(/\/ng\/work-items\/[0-9a-f]+$/); } },
  ] },
  // ux2-W6 F: the composer from the header button; a spec by its path; ⌘↵ sends the dry run, then the create with autostart.
  { name: "ng-composer-create", widths: [1280], mock: { ngBoard: true }, start: ngBoard(), steps: [
    { name: "open", run: async (p) => { await p.getByRole("button", { name: "+ New work item" }).click(); await expect(p.getByRole("textbox", { name: "Title" })).toBeFocused(); await p.getByText(/of 15 nodes run/).waitFor(); } },
    { name: "title", run: async (p) => { await p.keyboard.type("Design the caching layer for document search"); } },
    { name: "attach-spec", run: async (p) => {
      await p.getByRole("button", { name: "+ spec" }).click();
      await expect(p.getByRole("textbox", { name: /Search specs/ })).toBeFocused();
      await p.keyboard.type("docs/specs/doc-search-cache.md"); await p.waitForTimeout(300); await p.keyboard.press("Enter");
      await expect(p.getByText("13 of 15 nodes run · 4 gates")).toBeVisible();
    } },
    { name: "cmd-enter-creates-and-starts", run: async (p) => {
      const sent = p.waitForRequest((r) => r.method() === "POST" && /\/work-items$/.test(new URL(r.url()).pathname) && !r.url().includes("dry_run"));
      await p.getByRole("textbox", { name: "Title" }).focus();
      await p.keyboard.press("Control+Enter");
      const r = await sent;
      expect(r.postDataJSON()).toMatchObject({ title: "Design the caching layer for document search", autostart: true, attachments: [{ kind: "spec", path: "docs/specs/doc-search-cache.md" }] });
      await expect(p.getByRole("region", { name: "New work item" })).toHaveCount(0);
      await expect(p).toHaveURL(/[?&]sel=/);
    }, wait: 700 },
  ] },
  // ux2-W5 G (R6): chain → node view → a task's pane → back, keyboard only.
  { name: "ng-node-keyboard", widths: [1280], keyboard: true, start: ngItem("running"), steps: [
    { name: "tab-into-chain", run: async (p) => { await p.locator('.graph-node[tabindex="0"]').focus(); await expect(p.getByRole("button", { name: /^verification, node, running/ })).toBeFocused(); } },
    { name: "cmd-enter-node-view", run: async (p) => { await p.keyboard.press("ControlOrMeta+Enter"); await expect(p).toHaveURL(/\/nodes\/verification$/); await expect(p.getByRole("group", { name: "verification" })).toBeVisible(); await expect(p.locator('.graph-node[tabindex="0"]').first()).toBeFocused(); } },
    { name: "arrows-to-a-task", run: async (p) => { await p.keyboard.press("ArrowRight"); await p.keyboard.press("ArrowDown"); await expect(p.getByRole("button", { name: /^typecheck,/ })).toBeFocused(); } },
    { name: "enter-opens-pane", run: async (p) => { await p.keyboard.press("Enter"); await expect(p).toHaveURL(/sel=verification\.checks\.typecheck/); await expect(p.getByRole("complementary", { name: "typecheck pane" })).toBeVisible(); } },
    // Escape from the canvas collapses the pane and leaves focus on the canvas.
    { name: "escape-collapses", run: async (p) => { await p.getByRole("button", { name: /^typecheck,/ }).focus(); await p.keyboard.press("Escape"); await expect(p.getByRole("button", { name: "Expand pane" })).toBeVisible(); await expect(p.getByRole("button", { name: /^typecheck,/ })).toBeFocused(); } },
    { name: "escape-back-to-chain", run: async (p) => { await p.keyboard.press("Escape"); await expect(p).toHaveURL(/\/work-items\/[0-9a-f]+$/); await expect(p.locator('.graph-node[tabindex="0"]').first()).toBeFocused(); } },
  ] },
  // ux2-W5 H: retry a failed item from its failed task, through the task pane; the request names the task's path.
  { name: "ng-retry-task", widths: [1280], start: ngItem("failed"), steps: [
    { name: "open-node", run: async (p) => { await p.getByRole("button", { name: "Open merge_request →" }).click(); await expect(p.getByRole("complementary", { name: "merge_request pane" })).toBeVisible(); } },
    { name: "focus", run: async (p) => { await p.getByRole("button", { name: /Focus/ }).click(); await expect(p).toHaveURL(/\/nodes\/merge_request$/); } },
    { name: "task-pane", run: async (p) => { await p.getByRole("button", { name: /^open_draft,/ }).click(); await expect(p.getByRole("complementary", { name: "open_draft pane" })).toBeVisible(); await expect(p.getByText("attempt 3 of 3")).toBeVisible(); } },
    { name: "retry-confirm", run: async (p) => { await p.getByRole("complementary", { name: "open_draft pane" }).getByRole("button", { name: "Retry" }).click(); await expect(p.getByRole("group", { name: "Retry merge_request.open.open_draft" })).toBeVisible(); } },
    { name: "retry-sent", run: async (p) => {
      const sent = p.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/retry"));
      await p.getByRole("group", { name: "Retry merge_request.open.open_draft" }).getByRole("button", { name: "Retry" }).click();
      expect((await sent).postDataJSON()).toEqual({ path: "merge_request.open.open_draft" });
    } },
  ] },
  // ux2-W8 exit: a request-changes review, from the item page's diff line, sends the threads and starts the fix round on the right node; the item page shows it.
  { name: "ng-review-request-changes", widths: [1280], keyboard: true, start: ngItem("needs-gate"), steps: [
    { name: "open-review", run: async (p) => {
      await p.getByRole("link", { name: "Review changes" }).focus(); await p.keyboard.press("Enter");
      await expect(p.getByRole("heading", { name: /^Review changes:/ })).toBeAttached();
      await expect(p.getByRole("group", { name: /^Lines of / }).first()).toBeVisible();
    }, kbd: true },
    { name: "pick-a-line", run: async (p) => {
      await p.getByRole("group", { name: /^Lines of / }).first().focus();
      for (let i = 0; i < 3; i++) await p.keyboard.press("ArrowDown");
      await p.keyboard.press("Enter");
      await expect(p.getByRole("textbox", { name: "Comment" })).toBeFocused();
    }, kbd: true },
    { name: "must-fix-add", run: async (p) => {
      await p.keyboard.type("Keep the old signature until the callers move.");
      await p.getByRole("radio", { name: "Must fix" }).focus(); await p.keyboard.press("Space");
      const sent = p.waitForRequest((r) => r.method() === "POST" && /\/work-items\/[^/]+\/threads$/.test(r.url()));
      await p.getByRole("button", { name: "Add to review" }).focus(); await p.keyboard.press("Enter");
      expect((await sent).postDataJSON()).toMatchObject({ label: "must_fix", side: "new", start_line: 2, end_line: 2 });
      await expect(p.getByText("Keep the old signature until the callers move.")).toBeVisible();
    }, kbd: true },
    { name: "finish", run: async (p) => {
      await p.getByRole("button", { name: "Request changes" }).focus(); await p.keyboard.press("Enter");
      await expect(p.getByRole("dialog", { name: "Finish your review" })).toBeVisible();
      await expect(p.getByRole("radio", { name: /Request changes/ })).toBeChecked();
    }, kbd: true },
    { name: "submit", run: async (p) => {
      const sent = p.waitForRequest((r) => r.method() === "POST" && /\/review$/.test(r.url()));
      await p.getByRole("button", { name: "Submit review" }).focus(); await p.keyboard.press("Enter");
      const r = await sent;
      expect(new URL(r.url()).pathname).toMatch(/\/gates\/final_review\/review$/);
      expect(r.postDataJSON()).toEqual({ outcome: "request_changes" });
      await expect.poll(() => new URL(p.url()).search).toBe("?sel=implementation");
      await expect(p.getByText("RUNNING", { exact: true })).toBeVisible();
    }, wait: 700 },
  ] },
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
  { name: "sidebar-toggle", widths: [1280, 1100], start: board, steps: [
    // Under 1280 the sidebar starts as the rail (accepted, UI v3 · 45): there is no Collapse to press.
    { name: "collapse", run: async (p) => { const b = p.getByRole("button", { name: /collapse/i }); if (await b.count()) await b.click(); } },
    { name: "expand", run: async (p) => { await p.getByRole("button", { name: /expand/i }).click(); } },
    { name: "settings-group-open", run: async (p) => { await p.getByRole("button", { name: /settings/i }).first().click(); } },
    { name: "reload-persists", run: async (p) => { await p.getByRole("button", { name: /collapse/i }).click(); await p.reload(); await settle(p, 700); } },
  ] },
];

async function snapshotState(p: Page, anyFocus = false) {
  const s = await p.evaluate(() => ({
    url: location.pathname + location.hash,
    focused: (() => { const a = document.activeElement as HTMLElement | null; if (!a || a === document.body) return "body"; return `${a.tagName.toLowerCase()}${a.getAttribute("aria-label") ? `[${a.getAttribute("aria-label")}]` : ""} "${(a.textContent || "").trim().slice(0, 40)}"`; })(),
    negDurations: (() => { const out: string[] = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); for (let n = w.nextNode(); n; n = w.nextNode()) if (/-\d+s\b/.test(n.textContent || "")) out.push((n.textContent || "").trim().slice(0, 60)); return out; })(),
    focusRingVisible: (() => { const a = document.activeElement as HTMLElement | null; if (!a || a === document.body) return null; const cs = getComputedStyle(a); return cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0 || cs.boxShadow !== "none"; })(),
    dialogs: [...document.querySelectorAll('[role="dialog"], [aria-label="peek"]')].map((d) => d.getAttribute("aria-label") || "dialog"),
    toast: (document.querySelector(".toast, [role=status]")?.textContent || "").trim().slice(0, 80) || null,
    scrollY: window.scrollY,
    overflowX: document.documentElement.scrollWidth > innerWidth + 1,
  }));
  return { ...s, focusRingMissing: await focusRingMissing(p, anyFocus) };
}

for (const f of FLOWS) for (const width of f.widths) {
  test(`flow/${f.name}@${width}`, async ({ page }) => {
    const [w, h] = VP[width] ?? [width, 800];
    await page.setViewportSize({ width: w, height: h });
    const S = buildScenario(f.data ?? "default");
    await installMocks(page, S, f.mock);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message.slice(0, 200)));
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 200)); });
    const dir = path.join(OUT, `flow-${f.name}`); fs.mkdirSync(dir, { recursive: true });
    await f.start(page, S);
    let prev = await snapshotState(page);
    const shoot = async (i: number, name: string, err?: string, anyFocus = false) => {
      const file = `flow-${f.name}/${String(i).padStart(2, "0")}-${name}@${width}.png`;
      await page.screenshot({ path: path.join(OUT, file), animations: "disabled" }).catch(() => {});
      const now = await snapshotState(page, anyFocus).catch(() => prev);
      const changed = Object.keys(now).filter((k) => JSON.stringify((now as any)[k]) !== JSON.stringify((prev as any)[k]));
      fs.appendFileSync(MANIFEST, JSON.stringify({ id: file.replace(/\.png$/, ""), screen: `flow-${f.name}`, variant: `${String(i).padStart(2, "0")}-${name}`, data: f.data ?? "default", width: w, height: h, shell: "auto", file, url: page.url(), phone: width < 768, chromeMoved: [], step: { state: now, changed, error: err ?? null }, checks: { pageOverflowX: now.overflowX, offscreenRight: { count: 0, examples: [] }, clippedEllipsis: { count: 0, examples: [] }, clippedVertical: { count: 0, examples: [] }, smallTargets: { count: 0, examples: [] }, smallInputs: { count: 0, examples: [] }, nestedScrollers: { count: 0, examples: [] }, negativeDurations: { count: now.negDurations.length, examples: now.negDurations.slice(0, 8) }, lowContrast: { count: 0, examples: [] }, focusRingMissing: now.focusRingMissing, consoleErrors: [...errors], ...(err ? { setupError: err } : {}) }, at: new Date().toISOString() }) + "\n");
      prev = now;
    };
    await shoot(0, "start");
    for (let i = 0; i < f.steps.length; i++) {
      const s = f.steps[i];
      let err: string | undefined;
      try { await s.run(page, S); } catch (e) { err = (e instanceof Error ? e.message : String(e)).split("\n")[0].slice(0, 200); }
      await page.waitForTimeout(s.wait ?? 400);
      await shoot(i + 1, s.name, err, !!(f.keyboard || s.kbd));
    }
    expect(errors.filter((e) => e.startsWith("pageerror"))).toEqual([]);
  });
}
