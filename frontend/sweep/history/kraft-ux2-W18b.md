# ux2-W18 PR 2 · the cutover (F–M)

Branch `kraft/ux2-W18b`, on `b8fd6644d`. **Sweep port: 4392.**

## Baseline, renamed

`node sweep/wave.mjs all --baseline` in a detached worktree on `b8fd6644d` (final main): 2073 cells. Before the diff, that baseline was renamed to the cutover's names with this one-off script, so each new-UI cell is compared with its own earlier shot (912 kept, 1167 shipped cells dropped):

```js
// node rename-baseline.mjs <from baseline/all> <to baseline/ux2-W18>
import fs from "node:fs";
import path from "node:path";
const [from, to] = process.argv.slice(2);
const m = JSON.parse(fs.readFileSync(path.join(from, "manifest.json"), "utf8"));
const rename = (s) => s.startsWith("flow-ng-") ? "flow-" + s.slice(8) : s.startsWith("ng-") ? s.slice(3) : null;
fs.rmSync(to, { recursive: true, force: true });
const kept = [];
for (const e of m.entries) {
  const screen = rename(e.screen);
  if (!screen) continue;
  const swap = (x) => screen + x.slice(e.screen.length);
  const n = { ...e, screen, id: swap(e.id), file: swap(e.file) };
  const src = path.join(from, e.file);
  if (fs.existsSync(src)) { fs.mkdirSync(path.dirname(path.join(to, n.file)), { recursive: true }); fs.copyFileSync(src, path.join(to, n.file)); }
  kept.push(n);
}
fs.writeFileSync(path.join(to, "manifest.json"), JSON.stringify({ ...m, entries: kept }, null, 1));
```

## `node sweep/wave.mjs ux2-W18`

10/10 rules pass · 413 changed · 0 cleared · 0 regressions · 910 cells. Reviewed by the controller before the baseline reset (`DIFF-ux2-W18.md`, copied to the migration's SDD folder). Each changed cell was diffed again with pixelmatch and its changed-pixel box located:

- 378: the sidebar footer only ("Current UI ↗" gone, the version line moves down).
- 25: item-page cells: the footer, plus the brief's "more" toggle, which flips run to run (measured before the Inter webfont swaps in; Kraft-9d8b2.39).
- 5 new: `flow-old-addresses` (4) and the renamed `flow-phone-no-redirect/01-stays-on-the-board`.
- 3 intended: `appearance/derived` (the note names no palette), `shell/placeholder` and `~light` (the board link).
- 2 machine-dependent: `flow-apply-restart/05` and `flow-phone-access-restart/05` reload at the mock's saved port, 8765, which is this machine's installed Kraft (Kraft-9d8b2.41).

Harness fixes the full run turned up: #398's lanes flow names the sidebar by its class (`ng-sidebar`, which the rename had cut); a Chains node view draws no canvas, so its cells wait for the pane heading (they flickered in PR 1 too); the config no longer matches the deleted elements spec.

Baseline reset afterwards: `all` and `ux2-W18`, 916 cells.

## Playwright

`just e2e-ci` on the branch: nine specs plus `addresses.spec.ts`; the phone spec at 390 against a real server. A 14px `.ph-input` fails it on the font assertion.

## Upgrade smoke

Two scratch `KRAFT_HOME`s outside `/tmp`, each `templates/theme.yaml` = `palette: forest`, `mode: light`. Main (`b8fd6644d`) served `/ng`; the branch served `/`. GET /theme answered moss / green / full / light on both (main `derived: true`, the branch `derived: false`); the branch's file lost `palette` and gained the three keys, `theme.yaml.pre-ux2` held the original, and the log said so in one line. The two first-run pages at 1280 differ only in the port, the version and the removed "Current UI ↗".
