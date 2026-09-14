import { test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { buildScenario, STATES, type DisplayState, type Scenario } from "./fixtures";
import { installMocks } from "./mockApi";
import { runChecks } from "./checks";

/**
 * Element-level captures. Round 1 showed the item page's hero can push the
 * inspector and right pane below the fold, so a viewport shot never shows
 * the lists a person actually reads. This spec scrolls each region into
 * view and captures it on its own, plus one full-page shot per page.
 *
 * Appends to the same manifest.jsonl; screens are prefixed `el-`.
 */

const OUT = path.resolve("e2e-shots/sweep");
const MANIFEST = path.join(OUT, "manifest.jsonl");

const REGIONS: [string, string][] = [
  ["hero", ".detail-head, .item-hero, [data-testid=\"item-title\"]"],
  ["gate-card", "[data-testid=\"gate-card\"], [data-testid=\"escalated-card\"], [data-testid=\"escalating-pill\"]"],
  ["action-bar", ".action-bar, .item-actions"],
  ["stage-graph", ".stage-graph"],
  ["inspector", "[data-testid=\"inspector\"]"],
  ["right-pane", ".item-right-pane, .pane"],
];
const TABS = ["tasks", "changes", "documents", "timeline", "config"] as const;
const WIDTHS: Record<number, [number, number]> = { 390: [390, 844], 1100: [1100, 800], 1280: [1280, 800], 1920: [1920, 1080] };

const settle = (p: Page, ms = 500) => p.waitForTimeout(ms);
async function firstRow(page: Page, tab: string) {
  const sel: Record<string, string> = {
    tasks: '[data-testid^="task-row-"]', changes: '.tree-row[data-kind="file"]',
    documents: '[data-testid="inspector-documents"] li, [data-testid="inspector-documents"] button, [data-testid="inspector-documents"] [role="option"]',
    timeline: '[data-testid="inspector-timeline"] li, [data-testid="inspector-timeline"] button', config: "",
  };
  if (sel[tab]) await page.locator(sel[tab]).first().click({ timeout: 3000 }).catch(() => {});
  await settle(page);
}

interface Case { state: DisplayState; tab: typeof TABS[number]; data: "default" | "long"; widths: number[] }
const CASES: Case[] = [];
for (const data of ["default", "long"] as const)
  for (const tab of TABS) CASES.push({ state: "gate", tab, data, widths: [390, 1100, 1280, 1920] });
for (const st of STATES) CASES.push({ state: st, tab: "tasks", data: "long", widths: [1280] });
CASES.push({ state: "running", tab: "documents", data: "long", widths: [390, 1280] });
CASES.push({ state: "capped", tab: "timeline", data: "long", widths: [390, 1280] });
CASES.push({ state: "done", tab: "changes", data: "long", widths: [390, 1280] });

const DECLARED = new Set<string>();
for (const cs of CASES) for (const width of cs.widths) {
  const base = `${cs.state}-${cs.tab}-${cs.data}@${width}`;
  // gate×tasks×long@1280 comes from both loops above; Playwright rejects duplicate titles.
  if (DECLARED.has(base)) continue;
  DECLARED.add(base);
  test(`el/${base}`, async ({ page }) => {
    const [w, h] = WIDTHS[width];
    await page.setViewportSize({ width: w, height: h });
    const S: Scenario = buildScenario(cs.data);
    await installMocks(page, S);
    const consoleErrors: string[] = [];
    page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 300)); });
    page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message.slice(0, 300)}`));
    await page.goto(`/work-items/${S.byState[cs.state].item.id}#tab=${cs.tab}`);
    await page.locator(".detail, .item-page, .phone-item").first().waitFor({ timeout: 8000 }).catch(() => {});
    await settle(page, 700);
    await firstRow(page, cs.tab);
    const checks = await runChecks(page, width < 768, consoleErrors).catch(() => null);

    // Full page first — the one view that shows how far the hero pushes things.
    const dirFull = path.join(OUT, "el-fullpage"); fs.mkdirSync(dirFull, { recursive: true });
    await page.screenshot({ path: path.join(dirFull, `${base}.png`), fullPage: true, animations: "disabled" }).catch(() => {});
    fs.appendFileSync(MANIFEST, JSON.stringify({ id: `el-fullpage/${base}`, screen: "el-fullpage", variant: `${cs.state}-${cs.tab}-${cs.data}`, data: cs.data, width: w, height: h, shell: "auto", file: `el-fullpage/${base}.png`, url: page.url(), phone: width < 768, chromeMoved: [], checks, at: new Date().toISOString() }) + "\n");

    for (const [name, sel] of REGIONS) {
      // A never-started item's default tab is the intake card, not the split
      // (Kraft-pfqdb): no inspector to shoot. el-fullpage above still shoots the card (Kraft-3xqc9).
      if (cs.state === "not_started" && name === "inspector") continue;
      const loc = page.locator(sel).first();
      // The split regions must exist on desktop; a missing or 0×0 region is
      // recorded as collapsed rather than skipped, or the W0 rule passes blind.
      const mustExist = width >= 768 && (name === "inspector" || name === "right-pane");
      if (!(await loc.count()) && !mustExist) continue;
      const dir = path.join(OUT, `el-${name}`); fs.mkdirSync(dir, { recursive: true });
      await loc.scrollIntoViewIfNeeded({ timeout: 2000 }).catch(() => {});
      await settle(page, 150);
      const box = (await loc.count()) ? await loc.boundingBox().catch(() => null) : null;
      const ok = box ? await loc.screenshot({ path: path.join(dir, `${base}.png`), animations: "disabled", timeout: 5000 }).then(() => true).catch(() => false) : false;
      if (!ok && !mustExist) continue;
      const collapsed = !ok || !box || box.width < 2 || box.height < 2;
      fs.appendFileSync(MANIFEST, JSON.stringify({
        id: `el-${name}/${base}`, screen: `el-${name}`, variant: `${cs.state}-${cs.tab}-${cs.data}`, data: cs.data, width: w, height: h, shell: "auto",
        file: `el-${name}/${base}.png`, url: page.url(), phone: width < 768, chromeMoved: [], element: { selector: sel, box, collapsed },
        checks: { ...(checks ?? {}), consoleErrors }, at: new Date().toISOString(),
      }) + "\n");
    }
  });
}
