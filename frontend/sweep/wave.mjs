#!/usr/bin/env node
/**
 * Wave acceptance in one command:
 *   node sweep/wave.mjs W0            # re-shoot W0's cells, diff vs baseline, write DIFF-W0.md
 *   node sweep/wave.mjs W0 --baseline # snapshot current shots as the baseline for W0 (run BEFORE fixing)
 *   node sweep/wave.mjs all --baseline
 *
 * Baselines live in e2e-shots/baseline/<wave>/ (PNGs + manifest.json). The diff
 * is pixel-level (pixelmatch if installed, else a size+byte compare) and the
 * report lists: cells that changed, cells still flagged, cells newly flagged,
 * and the wave's acceptance rules with pass/fail.
 */
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { PNG } from "pngjs";

const [, , waveArg = "all", ...flags] = process.argv;
const BASELINE = flags.includes("--baseline");
const WAVES = JSON.parse(fs.readFileSync(path.resolve("sweep/waves.json"), "utf8"));
const wave = waveArg === "all" ? null : WAVES[waveArg];
if (waveArg !== "all" && !wave) { console.error(`unknown wave ${waveArg}; known: ${Object.keys(WAVES).join(", ")}`); process.exit(2); }

const OUT = path.resolve("e2e-shots/sweep");
const BASE = path.resolve("e2e-shots/baseline", waveArg);
// "all" (W10) is every screen and every spec, same as `wave.mjs all`.
const screens = wave && wave.screens !== "all" ? wave.screens : null;

let pixelmatch = null;
try { pixelmatch = (await import("pixelmatch")).default; } catch { /* fallback compare */ }

function collect() {
  execSync("node sweep/collect.mjs", { stdio: "inherit" });
  return JSON.parse(fs.readFileSync(path.join(OUT, "manifest.json"), "utf8"));
}
// A trailing `*` (W11's "el-*") is a prefix: every screen that starts with what comes before it.
const inScope = (e) => !screens || screens.some((s) => s.endsWith("*") ? e.screen.startsWith(s.slice(0, -1)) : e.screen === s || e.screen.startsWith(s + "-") || e.id.startsWith(s));

if (BASELINE) {
  const m = collect();
  fs.rmSync(BASE, { recursive: true, force: true });
  fs.mkdirSync(BASE, { recursive: true });
  const entries = m.entries.filter(inScope);
  for (const e of entries) {
    const src = path.join(OUT, e.file);
    if (!fs.existsSync(src)) continue;
    fs.mkdirSync(path.dirname(path.join(BASE, e.file)), { recursive: true });
    fs.copyFileSync(src, path.join(BASE, e.file));
  }
  fs.writeFileSync(path.join(BASE, "manifest.json"), JSON.stringify({ ...m, entries }, null, 1));
  console.log(`baseline ${waveArg}: ${entries.length} cells → ${BASE}`);
  process.exit(0);
}

// 1. Re-shoot the wave's screens.
const env = { ...process.env };
if (screens) env.SWEEP_SCREEN = screens.map((s) => s.replace(/\*$/, "")).join(",");
const specs = wave?.specs && wave.specs !== "all" ? wave.specs : ["sweep.spec.ts", "elements.spec.ts", "interactions.spec.ts"];
for (const spec of specs) {
  try { execSync(`npx playwright test -c sweep/playwright.sweep.config.ts sweep/${spec}`, { stdio: "inherit", env }); }
  catch { /* the specs never assert; a non-zero exit is a harness crash and is visible in the list reporter */ }
}
const m = collect();
const after = m.entries.filter(inScope);
const baseM = fs.existsSync(path.join(BASE, "manifest.json")) ? JSON.parse(fs.readFileSync(path.join(BASE, "manifest.json"), "utf8")) : null;
const before = new Map((baseM?.entries ?? []).map((e) => [e.id, e]));

// 2. Pixel diff.
function diffPng(a, b) {
  if (!fs.existsSync(a) || !fs.existsSync(b)) return { changed: true, pct: 100, reason: "missing" };
  const A = PNG.sync.read(fs.readFileSync(a)), B = PNG.sync.read(fs.readFileSync(b));
  if (A.width !== B.width || A.height !== B.height) return { changed: true, pct: 100, reason: `size ${A.width}x${A.height}→${B.width}x${B.height}` };
  if (!pixelmatch) { const same = Buffer.compare(A.data, B.data) === 0; return { changed: !same, pct: same ? 0 : -1, reason: same ? "" : "bytes differ (install pixelmatch for %)" }; }
  const n = pixelmatch(A.data, B.data, null, A.width, A.height, { threshold: 0.1 });
  const pct = (100 * n) / (A.width * A.height);
  return { changed: pct > 0.05, pct: +pct.toFixed(2), reason: "" };
}
const rows = after.map((e) => {
  const b = before.get(e.id);
  const d = b ? diffPng(path.join(BASE, e.file), path.join(OUT, e.file)) : { changed: true, pct: 100, reason: "new cell" };
  return { e, b, d };
});

// 3. Acceptance rules.
const flagsOf = (e) => (e.flags ?? []).filter((f) => !/^nested-scroll/.test(f));
const results = [];
const rule = (name, pass, detail = "") => results.push({ name, pass, detail });
for (const r of wave?.rules ?? []) {
  const cells = after.filter((e) => new RegExp(r.match).test(e.id));
  if (r.kind === "no-flag") {
    const bad = cells.filter((e) => flagsOf(e).some((f) => f.startsWith(r.flag)));
    rule(`${r.flag} = 0 on /${r.match}/`, bad.length === 0, bad.slice(0, 8).map((e) => e.id).join(", "));
  }
  if (r.kind === "no-flag-increase") {
    // A cell flagged before may stay flagged, but its count must not rise.
    const n = (e) => { const f = flagsOf(e).find((x) => x.startsWith(r.flag)); return f ? Number(f.split("×")[1] ?? 1) : 0; };
    const bad = cells.filter((e) => before.has(e.id) && n(e) > n(before.get(e.id)));
    rule(`${r.flag} count does not rise on /${r.match}/`, bad.length === 0, bad.slice(0, 8).map((e) => `${e.id} (${n(before.get(e.id))}→${n(e)})`).join(", "));
  }
  if (r.kind === "not-collapsed") {
    const bad = cells.filter((e) => e.element?.collapsed || (e.element?.box?.height ?? 9999) < (r.min ?? 1));
    rule(`not collapsed, height ≥ ${r.min ?? 1} on /${r.match}/`, bad.length === 0, bad.slice(0, 8).map((e) => `${e.id} (${e.element?.box?.height ?? "?"}px)`).join(", "));
  }
  if (r.kind === "flow-completes") {
    const bad = cells.filter((e) => e.step?.error);
    rule(`flow steps complete on /${r.match}/`, bad.length === 0, bad.slice(0, 8).map((e) => `${e.id}: ${e.step.error}`).join("; "));
  }
  if (r.kind === "differs") {
    const a = after.find((e) => e.id === r.a), b2 = after.find((e) => e.id === r.b);
    const d = a && b2 ? diffPng(path.join(OUT, a.file), path.join(OUT, b2.file)) : { changed: false };
    rule(`${r.a} differs from ${r.b}`, !!d.changed, d.reason);
  }
  if (r.kind === "no-console") {
    const bad = cells.filter((e) => (e.checks?.consoleErrors ?? []).length);
    rule(`no console errors on /${r.match}/`, bad.length === 0, bad.slice(0, 5).map((e) => e.id).join(", "));
  }
}
const regressions = rows.filter(({ e, b }) => b && flagsOf(b).length === 0 && flagsOf(e).length > 0);
rule("no newly flagged cells", regressions.length === 0, regressions.slice(0, 10).map(({ e }) => `${e.id}: ${flagsOf(e).join(",")}`).join("; "));

// 4. Report.
const changed = rows.filter((r) => r.d.changed);
const stillFlagged = rows.filter(({ e }) => flagsOf(e).length);
const cleared = rows.filter(({ e, b }) => b && flagsOf(b).length && !flagsOf(e).length);
let md = `# ${waveArg} — sweep diff\n\n${new Date().toISOString()} · ${after.length} cells in scope · baseline ${baseM ? baseM.generated : "none"}\n\n`;
md += `## Acceptance\n\n${results.map((r) => `- ${r.pass ? "PASS" : "FAIL"} · ${r.name}${r.detail ? ` — ${r.detail}` : ""}`).join("\n")}\n\n`;
md += `**${results.every((r) => r.pass) ? "ALL PASS" : results.filter((r) => !r.pass).length + " FAILING"}**\n\n`;
md += `## Summary\n\n- changed: ${changed.length}\n- flags cleared: ${cleared.length}\n- still flagged: ${stillFlagged.length}\n- newly flagged (regressions): ${regressions.length}\n\n`;
const side = (r) => `| \`${r.e.id}\` | ${r.b ? `![](../baseline/${waveArg}/${r.e.file})` : "—"} | ![](sweep/${r.e.file}) | ${r.d.pct >= 0 ? r.d.pct + "%" : r.d.reason} | ${flagsOf(r.e).join(", ") || "clean"} |`;
md += `## Regressions\n\n${regressions.length ? "| cell | before | after | Δ | flags |\n|---|---|---|---|---|\n" + regressions.map(side).join("\n") : "none"}\n\n`;
md += `## Still flagged (look at these)\n\n${stillFlagged.length ? "| cell | before | after | Δ | flags |\n|---|---|---|---|---|\n" + stillFlagged.slice(0, 60).map(side).join("\n") : "none"}\n\n`;
md += `## Changed, now clean\n\n${cleared.length ? "| cell | before | after | Δ | flags |\n|---|---|---|---|---|\n" + cleared.slice(0, 60).map(side).join("\n") : "none"}\n\n`;
md += `## Unchanged\n\n${rows.length - changed.length} cells identical to baseline.\n`;
fs.writeFileSync(path.resolve("e2e-shots", `DIFF-${waveArg}.md`), md);
fs.writeFileSync(path.resolve("e2e-shots", `DIFF-${waveArg}.json`), JSON.stringify({ wave: waveArg, results, changed: changed.map((r) => r.e.id), cleared: cleared.map((r) => r.e.id), stillFlagged: stillFlagged.map((r) => ({ id: r.e.id, flags: flagsOf(r.e) })), regressions: regressions.map((r) => ({ id: r.e.id, flags: flagsOf(r.e) })) }, null, 1));
console.log(`\n${waveArg}: ${results.filter((r) => r.pass).length}/${results.length} rules pass · ${changed.length} changed · ${cleared.length} cleared · ${regressions.length} regressions → e2e-shots/DIFF-${waveArg}.md`);
process.exit(results.every((r) => r.pass) ? 0 : 1);
