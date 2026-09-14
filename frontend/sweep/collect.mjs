#!/usr/bin/env node
/**
 * Turn e2e-shots/sweep/manifest.jsonl into manifest.json + FINDINGS.md.
 *   node sweep/collect.mjs [e2e-shots/sweep]
 */
import fs from "node:fs";
import path from "node:path";

const dir = path.resolve(process.argv[2] ?? "e2e-shots/sweep");
const lines = fs.readFileSync(path.join(dir, "manifest.jsonl"), "utf8").split("\n").filter(Boolean);
// Last write wins per id (re-runs append).
const byId = new Map();
for (const l of lines) { const e = JSON.parse(l); byId.set(e.id, e); }
const entries = [...byId.values()].sort((a, b) => a.screen.localeCompare(b.screen) || a.variant.localeCompare(b.variant) || a.width - b.width);

const flag = (e) => {
  const c = e.checks, f = [];
  if (c.setupError) f.push("setup");
  if (c.pageOverflowX) f.push("overflow-x");
  if (c.offscreenRight.count) f.push(`offscreen×${c.offscreenRight.count}`);
  if (c.clippedEllipsis.count) f.push(`ellipsis×${c.clippedEllipsis.count}`);
  if (c.clippedVertical.count) f.push(`clipped-v×${c.clippedVertical.count}`);
  if (c.smallTargets.count) f.push(`target<44×${c.smallTargets.count}`);
  if (c.smallInputs.count) f.push(`input<16×${c.smallInputs.count}`);
  if (c.nestedScrollers.count) f.push(`nested-scroll×${c.nestedScrollers.count}`);
  if (c.negativeDurations?.count) f.push(`neg-duration×${c.negativeDurations.count}`);
  if (c.lowContrast?.count) f.push(`contrast×${c.lowContrast.count}`);
  if (c.focusRingMissing) f.push("focus-ring");
  if (c.consoleErrors.length) f.push(`console×${c.consoleErrors.length}`);
  if (e.chromeMoved?.length) f.push(`chrome-moved:${e.chromeMoved.join("/")}`);
  return f;
};
for (const e of entries) e.flags = flag(e);

const screens = [...new Set(entries.map((e) => e.screen))];
const widths = [...new Set(entries.map((e) => e.width))].sort((a, b) => a - b);
fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify({ generated: new Date().toISOString(), screens, widths, entries }, null, 1));

/* FINDINGS.md — what a human should look at first */
const tally = {};
for (const e of entries) for (const f of e.flags) { const k = f.split("×")[0].split(":")[0]; tally[k] = (tally[k] ?? 0) + 1; }
const flagged = entries.filter((e) => e.flags.length);
const byScreen = {};
for (const e of flagged) (byScreen[e.screen] ??= []).push(e);

let md = `# Sweep findings\n\n${entries.length} shots · ${flagged.length} flagged\n\n## By check\n\n`;
for (const [k, n] of Object.entries(tally).sort((a, b) => b[1] - a[1])) md += `- ${k}: ${n} shots\n`;
md += `\n## Worst screens\n\n`;
for (const [s, es] of Object.entries(byScreen).sort((a, b) => b[1].length - a[1].length)) {
  md += `### ${s} (${es.length}/${entries.filter((e) => e.screen === s).length} flagged)\n\n`;
  for (const e of es) {
    md += `- \`${e.variant}@${e.width}${e.shell !== "auto" ? "~" + e.shell : ""}\` — ${e.flags.join(", ")}\n`;
    const c = e.checks;
    const ex = [...c.offscreenRight.examples.slice(0, 3), ...c.clippedEllipsis.examples.slice(0, 3), ...c.smallTargets.examples.slice(0, 3), ...(c.negativeDurations?.examples ?? []).slice(0, 3), ...(c.lowContrast?.examples ?? []).slice(0, 3)];
    for (const x of ex) md += `    - ${x}\n`;
    if (c.setupError) md += `    - setup: ${c.setupError}\n`;
    for (const x of c.consoleErrors.slice(0, 2)) md += `    - console: ${x}\n`;
  }
  md += "\n";
}
md += `## Width sensitivity\n\n| screen | ${widths.join(" | ")} |\n|---|${widths.map(() => "---").join("|")}|\n`;
for (const s of screens) {
  md += `| ${s} | ${widths.map((w) => { const es = entries.filter((e) => e.screen === s && e.width === w); if (!es.length) return "·"; const n = es.filter((e) => e.flags.length).length; return n ? `**${n}/${es.length}**` : `0/${es.length}`; }).join(" | ")} |\n`;
}
fs.writeFileSync(path.join(dir, "FINDINGS.md"), md);
console.log(`${entries.length} shots, ${flagged.length} flagged → ${path.join(dir, "manifest.json")}, FINDINGS.md`);
