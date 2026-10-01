import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { buildScenario } from "./fixtures";
import { installMocks } from "./mockApi";
import { focusRingMissing } from "./checks";
import { loadFlows } from "./loadCases";

/**
 * Interaction flows: each flow is a list of steps; every step runs an action,
 * waits, screenshots, and records what changed (URL, focused element, dialog
 * open, toast text). Videos are on for every flow (playwright config project
 * `interactions`). Nothing asserts except "the page did not crash".
 *
 * Output: e2e-shots/sweep/flow-<name>/<nn>-<step>@<width>.png + manifest lines
 * with screen "flow-<name>".
 *
 * The flows live in sweep/cases/<wave>.flows.ts, one file per wave; this file lists none.
 */

const OUT = path.resolve("e2e-shots/sweep");
const MANIFEST = path.join(OUT, "manifest.jsonl");
const VP: Record<number, [number, number]> = { 390: [390, 844], 1280: [1280, 800] };

const FLOWS = await loadFlows();

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
