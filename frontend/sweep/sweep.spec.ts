import { test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { buildScenario } from "./fixtures";
import { installMocks } from "./mockApi";
import { chromeRects, runChecks, scrollAllToBottom, type Checks } from "./checks";
import { SHELLS_1280, shellId, type Case, type Shell } from "./cellKit";
import { loadCells } from "./loadCases";

/**
 * The UI sweep: every screen × data variant × viewport × shell state, each
 * screenshotted and machine-checked, appended to e2e-shots/sweep/manifest.jsonl.
 * Nothing here asserts — a failed setup is recorded on the manifest entry and
 * the shot is still taken, so one broken selector never hides 300 screens.
 *
 * Filter with env: SWEEP_SCREEN=board,item  SWEEP_WIDTHS=390,1280  SWEEP_VARIANT=long
 *
 * The cells live in sweep/cases/<wave>.ts, one file per wave; this file lists none.
 */

const OUT = path.resolve("e2e-shots/sweep");
fs.mkdirSync(OUT, { recursive: true });
const MANIFEST = path.join(OUT, "manifest.jsonl");

const VP: Record<number, [number, number]> = {
  390: [390, 844], 768: [768, 1024], 960: [960, 800], 1000: [1000, 800], 1024: [1024, 768], 1100: [1100, 800],
  1280: [1280, 800], 1440: [1440, 900], 1920: [1920, 1080],
};

const CASES: Case[] = await loadCells();

/* ── run ─────────────────────────────────────────────────────────────────── */

const only = (env: string | undefined) => (env ? env.split(",").map((s) => s.trim()) : null);
const SCREENS = only(process.env.SWEEP_SCREEN);
const WIDTHS = only(process.env.SWEEP_WIDTHS)?.map(Number);
const VARIANT = only(process.env.SWEEP_VARIANT);

// Every screen gets one ~light cell at 1280: its first case.
const FIRST_OF_SCREEN = new Set<Case>();
{ const seen = new Set<string>(); for (const cs of CASES) if (!seen.has(cs.screen)) { seen.add(cs.screen); FIRST_OF_SCREEN.add(cs); } }

for (const cs of CASES) {
  if (SCREENS && !SCREENS.some((s) => cs.screen.startsWith(s))) continue;
  if (VARIANT && !VARIANT.some((v) => cs.variant.startsWith(v))) continue;
  const shells: Shell[] = [{}, ...(cs.shells ?? [])];
  const combos: [number, Shell][] = [];
  for (const width of cs.widths) for (const shell of shells) {
    // Shell variants only run at 1280 unless the case pins its own widths.
    if (Object.keys(shell).length && !cs.shells?.length) continue;
    if (Object.keys(shell).length && width !== 1280 && cs.shells === SHELLS_1280) continue;
    combos.push([width, shell]);
  }
  if (FIRST_OF_SCREEN.has(cs) && !combos.some(([w, s]) => w === 1280 && s.mode === "light")) combos.push([1280, { mode: "light" }]);
  if (cs.firstpaint) combos.push([1280, { mode: "light", firstpaint: true }]);
  for (const [width, shell] of combos) {
    if (WIDTHS && !WIDTHS.includes(width)) continue;
    {
      const id = `${cs.screen}/${cs.variant}@${width}${shellId(shell) === "auto" ? "" : "~" + shellId(shell)}`;
      test(id, async ({ page }, testInfo) => {
        const [w, h0] = VP[width];
        const h = shell.short ? 700 : h0;
        await page.setViewportSize({ width: w, height: h });
        const S = buildScenario(cs.data, { mode: shell.mode, density: shell.density, group_by: shell.group_by });
        await installMocks(page, S, { locked: cs.locked, login: cs.login, ...cs.mock });
        await page.addInitScript((sb) => {
          if (sb) localStorage.setItem("kraft.sidebar_collapsed", sb === "rail" ? "true" : "false");
          else localStorage.removeItem("kraft.sidebar_collapsed");
        }, shell.sidebar ?? "");
        // A locked page cannot read /api/theme, so the mode shell reaches it the
        // only way a real one does: the theme this browser saved last session.
        if (cs.locked && shell.mode) await page.addInitScript((mode) => { localStorage.setItem("kraft.theme", JSON.stringify({ palette: "nocturne", mode })); localStorage.setItem("kraft.theme.v2", JSON.stringify({ surface: "graphite", mode })); }, shell.mode);
        const consoleErrors: string[] = [];
        page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 300)); });
        page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message.slice(0, 300)}`));

        let setupError: string | undefined;
        let scrolledChrome: Awaited<ReturnType<typeof chromeRects>> | null = null;
        let before: Awaited<ReturnType<typeof chromeRects>> | null = null;
        try {
          await cs.run({ page, S, width, shell });
          // A returning visitor's first paint: the app has run once in this
          // browser, so whatever it persists is there; shoot before React mounts.
          // /api/theme is held back on that reload so the frame is the one before
          // React mounts (a slow server), not the finished page.
          if (shell.firstpaint) {
            await page.route(/\/api\/theme$/, async (r) => { await new Promise((res) => setTimeout(res, 1500)); await r.fallback(); });
            await page.reload({ waitUntil: "domcontentloaded" });
          }
          if (cs.variant.includes("scrolled")) { before = await chromeRects(page); await scrollAllToBottom(page); scrolledChrome = await chromeRects(page); }
        } catch (e) {
          setupError = e instanceof Error ? e.message.split("\n")[0].slice(0, 300) : String(e);
        }
        const file = `${cs.screen}/${cs.variant}@${width}${shellId(shell) === "auto" ? "" : "~" + shellId(shell)}.png`;
        fs.mkdirSync(path.join(OUT, cs.screen), { recursive: true });
        await page.screenshot({ path: path.join(OUT, file), fullPage: cs.fullPage ?? false, animations: "disabled", caret: "hide" }).catch(() => {});
        const checks: Checks = await runChecks(page, width < 768, consoleErrors).catch((e) => ({
          pageOverflowX: false, offscreenRight: { count: 0, examples: [] }, clippedEllipsis: { count: 0, examples: [] }, clippedVertical: { count: 0, examples: [] },
          smallTargets: { count: 0, examples: [] }, smallInputs: { count: 0, examples: [] }, nestedScrollers: { count: 0, examples: [] },
          negativeDurations: { count: 0, examples: [] }, lowContrast: { count: 0, examples: [] }, consoleErrors, setupError: `checks failed: ${e}`,
        }));
        if (setupError) checks.setupError = setupError;
        const chromeMoved = before && scrolledChrome ? Object.keys(before).filter((k) => (before as any)[k] !== null && (before as any)[k] !== (scrolledChrome as any)[k]) : [];
        const entry = {
          id, screen: cs.screen, variant: cs.variant, data: cs.data, width: w, height: h, shell: shellId(shell), file,
          url: page.url(), phone: width < 768, chromeMoved, checks, at: new Date().toISOString(),
        };
        fs.appendFileSync(MANIFEST, JSON.stringify(entry) + "\n");
        testInfo.annotations.push({ type: "sweep", description: file });
      });
    }
  }
}
