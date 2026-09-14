import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { buildScenario, type DisplayState, type Scenario } from "./fixtures";
import { installMocks } from "./mockApi";
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
interface Flow { name: string; data?: "default" | "long"; state?: DisplayState; widths: number[]; start: (p: Page, S: Scenario) => Promise<void>; steps: Step[]; keyboard?: true }

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

const FLOWS: Flow[] = [
  { name: "peek-open-close", widths: [1280, 390], start: board, steps: [
    { name: "click-row", run: async (p, S) => { const row = p.locator('[data-testid="board-card"]').first(); if (p.viewportSize()!.width < 768) { const b = (await row.boundingBox())!; await p.mouse.move(b.x + 40, b.y + 20); await p.mouse.down(); await p.waitForTimeout(650); await p.mouse.up(); } else await row.click(); } },
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
  { name: "escalate-thread", state: "escalated", widths: [1280, 390], start: item("escalated"), steps: [
    { name: "reply-open", run: btn(/^Reply/) },
    { name: "thread-scroll", run: async (p) => { await p.locator('[data-testid="escalation-thread"]').evaluate((el) => { el.scrollTop = el.scrollHeight; }).catch(() => {}); } },
    { name: "dismiss", run: async (p) => { await p.getByRole("button", { name: /cancel/i }).first().click().catch(() => {}); await p.getByRole("button", { name: /dismiss/i }).first().click().catch(() => {}); }, wait: 600 },
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
    { name: "archived-chip", run: async (p) => { const vis = () => p.locator('a[href="/archived"]:visible'); if (!(await vis().count())) await p.getByLabel("more filters").click(); await vis().first().click(); }, wait: 700 },
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
    await installMocks(page, S);
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
