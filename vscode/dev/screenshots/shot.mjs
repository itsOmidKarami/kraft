// Retake the Marketplace screenshots in vscode/media/ (`just vscode-screenshots`).
//
// Packages the extension, starts a throwaway Kraft on a seeded home with fake agents (the same
// setup test/integration/runner.mjs uses), stages a demo change and findings on the gate item,
// drives the packaged extension in a real VS Code through Playwright, and writes the crops.
//
//   node shot.mjs [--out DIR] [--vsix FILE] [--keep]
//
// --out   where the PNGs go (default: vscode/media)
// --vsix  install this package instead of building one from the working tree
// --keep  leave the Kraft home behind, to look at what the run saw
import { spawn, spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { parseArgs } from "node:util";
import { _electron } from "playwright-core";
import sharp from "sharp";

const { values: opts } = parseArgs({ options: { out: { type: "string" }, vsix: { type: "string" }, keep: { type: "boolean" } } });
const EXT = resolve(import.meta.dirname, "../..");
const ROOT = resolve(EXT, "..");
const OUT = resolve(opts.out ?? join(EXT, "media"));
mkdirSync(OUT, { recursive: true });

// The demo change on the gate item, and the spec it implements.
const SPEC = `
# Caching layer

## Problem

Every call to \`add()\` in the pricing path recomputes a result it computed a
moment ago. Under load the service spends most of its time on repeat work.

## Design

- A small in-process cache, \`cache.py\`, keyed on the call's arguments.
- Entries expire after a configurable TTL (default 60 seconds).
- The cache holds at most \`max_entries\` results; the oldest is evicted first.

## Out of scope

- A shared cache across processes (Redis, memcached).
- Invalidation on write: the functions cached here are pure.

## Testing

- A hit returns the stored value without calling the function.
- An entry older than the TTL is recomputed.
- Filling past \`max_entries\` evicts the oldest entry.
`;

const CACHE = `import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, ttl=60.0, max_entries=1024):
        self.ttl = ttl
        self.max_entries = max_entries
        self._entries = OrderedDict()

    def get(self, key, compute):
        hit = self._entries.get(key)
        if hit and time.monotonic() - hit[1] < self.ttl:
            return hit[0]
        value = compute()
        self._entries[key] = (value, time.monotonic())
        if len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        return value
`;

const CALC = `from cache import TTLCache

_cache = TTLCache()


def add(a, b):
    return _cache.get(("add", a, b), lambda: a + b)
`;

const sh = (cmd, args, cwd, env = process.env) => {
  const r = spawnSync(cmd, args, { cwd, env, stdio: "inherit" });
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(" ")} failed`);
};

// ---- the package: `vsce package` does not rebuild dist/, so build first.
const scratch = mkdtempSync(join(tmpdir(), "kvs-")); // short: VS Code's IPC socket path must fit in ~100 chars
let vsix = opts.vsix && resolve(opts.vsix);
if (!vsix) {
  vsix = join(scratch, "kraft.vsix");
  sh("npm", ["run", "build"], EXT);
  sh("npx", ["vsce", "package", "--out", vsix], EXT);
}

// ---- a throwaway Kraft, seeded. Outside /tmp: macOS tmp paths trip the workers' sandbox profile.
const home = mkdtempSync(join(ROOT, ".kraft-vscode-it-shot-"));
const port = await new Promise((ok) => {
  const s = createServer().listen(0, () => {
    const { port } = s.address();
    s.close(() => ok(port));
  });
});
const env = { ...process.env, KRAFT_HOME: home, KRAFT_BD_CWD: join(home, "repo"), KRAFT_PORT: String(port), PATH: `${join(ROOT, "fixtures", "bin")}:${process.env.PATH}` };
const api = `http://127.0.0.1:${port}/api`;
cpSync(join(ROOT, "config"), join(home, "config"), { recursive: true });
rmSync(join(home, "config", "access.yaml"), { force: true });
sh("uv", ["run", "python", "dev/seed.py", "--repo-only"], ROOT, env);
const daemon = spawn("uv", ["run", "python", "-m", "kraft"], { cwd: ROOT, env, stdio: "inherit" });
let app;
try {
  for (let i = 0; i < 120; i++) {
    if (await fetch(`${api}/health`).then((r) => r.ok, () => false)) break;
    await new Promise((r) => setTimeout(r, 500));
  }
  sh("uv", ["run", "python", "dev/seed.py"], ROOT, env);
  await stage();
  app = await launch();
  await drive(app);
  // The docs show three of these too: copy them there when they were written to the Marketplace's folder.
  if (OUT === join(EXT, "media")) {
    for (const f of ["board", "gate", "review"]) cpSync(join(OUT, `screenshot-${f}.png`), join(ROOT, ".github", "assets", `vscode-${f}.png`));
  }
} finally {
  await app?.close().catch(() => {});
  const exited = new Promise((r) => daemon.once("exit", r));
  daemon.kill("SIGTERM");
  await exited;
  if (!opts.keep) rmSync(home, { recursive: true, force: true });
  else console.log(`kept ${home}`);
  rmSync(scratch, { recursive: true, force: true });
}

// ---- the demo: plain titles, a spec, a change and three minor findings on the gate item.
async function stage() {
  const { items } = await (await fetch(`${api}/work-items`)).json();
  const gate = items.find((i) => i.title === "Design the caching layer");
  if (!gate) throw new Error("the seed made no gate item");
  const wt = join(home, "run", "worktrees", gate.id);
  // The documents live outside the worktree: run/items/<id>/docs/<kind>.md.
  const docs = join(home, "run", "items", gate.id, "docs");
  for (const f of readdirSync(docs)) writeFileSync(join(docs, f), SPEC);
  writeFileSync(join(wt, "cache.py"), CACHE);
  writeFileSync(join(wt, "calc.py"), CALC);
  const db = new DatabaseSync(join(home, "run", "orchestrator.db"));
  db.exec("update work_items set title='Rewrite the config parser' where title like '%KRAFT_FAIL%'");
  db.exec("update work_items set title='Investigate the flaky import test' where title like '%KRAFT_SLOW%'");
  const findings = [
    { severity: "minor", message: "get() is not thread-safe: two callers that miss on the same key both compute it.", file: "cache.py", line: 12, source_plugin: "code-review" },
    { severity: "minor", message: "Eviction runs after the insert, so the cache briefly holds max_entries + 1 entries.", file: "cache.py", line: 17, source_plugin: "code-review" },
    { severity: "minor", message: "No test covers an entry expiring after its TTL.", file: null, line: null, source_plugin: "code-review" },
  ];
  db.prepare("insert into events(work_item_id, type, payload, created_at) values (?, ?, ?, ?)").run(gate.id, "findings_measured", JSON.stringify({ findings }), new Date().toISOString());
  db.close();
}

async function launch() {
  const require = createRequire(join(EXT, "package.json"));
  const { downloadAndUnzipVSCode } = require("@vscode/test-electron");
  const exe = await downloadAndUnzipVSCode({ cachePath: join(EXT, ".vscode-test"), version: "stable" });
  const ud = join(scratch, "ud");
  mkdirSync(join(ud, "User"), { recursive: true });
  writeFileSync(join(ud, "User", "settings.json"), JSON.stringify(SETTINGS(port), null, 2));
  // The CLI sits beside the app: Contents/Resources/app/bin/code on macOS, bin/code elsewhere.
  const cli = process.platform === "darwin" ? join(dirname(dirname(exe)), "Resources", "app", "bin", "code") : join(dirname(exe), "bin", "code");
  sh(cli, [`--user-data-dir=${ud}`, `--extensions-dir=${join(ud, "ext")}`, "--install-extension", vsix], EXT);
  return _electron.launch({
    executablePath: exe,
    // No forced scale factor: on xvfb it halved the window's CSS size, so text came out twice its size.
    args: [
      join(home, "repo"), `--user-data-dir=${ud}`, `--extensions-dir=${join(ud, "ext")}`, "--skip-welcome", "--skip-release-notes", "--disable-workspace-trust",
    ],
    env,
  });
}

async function drive(app) {
  const page = await app.firstWindow();
  await app.evaluate(({ BrowserWindow }) => {
    const w = BrowserWindow.getAllWindows()[0];
    // A laptop's window, so text sits at the size a person sees it.
    w.setContentSize(1440, 900);
    w.center();
  });
  await page.waitForSelector(".statusbar", { timeout: 60_000 });
  await page.waitForTimeout(6000);
  const pause = (ms) => page.waitForTimeout(ms);
  const palette = async (cmd) => {
    await page.keyboard.press("F1");
    await page.waitForSelector(".quick-input-widget", { state: "visible" });
    await page.keyboard.type(cmd);
    await pause(600);
    await page.keyboard.press("Enter");
    await pause(800);
  };
  // Clips are in CSS pixels, and so is the PNG: a Retina display's 2x render is scaled down to the
  // clip's own size, so a picture shows VS Code at the size it is on screen, not zoomed in.
  const shot = async (name, clip) => {
    // VS Code as root (a container) ends the window title in " [Superuser]", whatever `window.title` says.
    await page.evaluate(() => {
      for (const n of document.querySelectorAll(".window-title")) n.textContent = n.textContent.replace(/ \[Superuser\]$/, "");
    });
    // The whole window, then the clip cut from it: a clipped capture closes an open hover first.
    const full = await page.screenshot();
    const size = await sharp(full).metadata();
    const k = size.width / (await page.evaluate(() => window.innerWidth));
    const [left, top] = [clip.x, clip.y].map((n) => Math.round(n * k));
    // Rounded apart, a clip that reaches the window's edge can end a pixel past it, which extract refuses:
    // that pixel is trimmed. A clip further off the window is a broken shot, so it fails here, by name.
    const fit = (start, span, whole) => {
      const end = Math.round((start + span) * k);
      if (start < 0 || end > whole + 1) throw new Error(`${name}: clip ${JSON.stringify(clip)} is outside the window`);
      return Math.min(end, whole) - Math.round(start * k);
    };
    const width = fit(clip.x, clip.width, size.width);
    const height = fit(clip.y, clip.height, size.height);
    let img = sharp(await sharp(full).extract({ left, top, width, height }).toBuffer());
    // The clip's width in CSS pixels, rounded as `fit` rounds it, so a 1x capture is never resampled.
    const css = Math.round(clip.x + clip.width) - Math.round(clip.x);
    if (width > css) img = sharp(await img.resize({ width: css }).toBuffer());
    await img.png({ palette: true, colours: 256, dither: 0 }).toFile(join(OUT, `${name}.png`));
    console.log(`wrote ${join(OUT, `${name}.png`)}`);
  };

  await page.click('.activitybar [aria-label^="Kraft"]');
  await pause(3000);
  // Only the gate's toast: close any other (the Red Hat YAML nudge) that would stack on it.
  const others = page.locator(".notification-toast").filter({ hasNotText: "approve spec" });
  // The clear button only shows while the toast is hovered (VS Code 1.141).
  while (await others.count()) {
    const o = others.first();
    await o.hover();
    await o.locator(".codicon-notifications-clear").click();
  }
  const toast = page.locator(".notification-toast", { hasText: "approve spec" });
  await page.mouse.move(0, 0); // off the close button it just clicked
  await shot("screenshot-gate-notification", await toast.boundingBox());
  await toast.getByRole("button", { name: "Open" }).click();
  await pause(2500);
  // Wide enough to read a row's reason after its title, in this shot and the rest: a 1440 window has the room.
  const side = await page.locator(".part.sidebar").boundingBox();
  await page.mouse.move(side.x + side.width, side.y + 300);
  await page.mouse.down();
  await page.mouse.move(side.x + side.width + 120, side.y + 300, { steps: 8 });
  await page.mouse.up();
  await page.mouse.move(0, 0);
  await pause(1000);
  await shot("screenshot-gate", { x: 0, y: 0, width: 1440, height: 640 });
  // The sidebar in its window: the activity bar and title bar keep it the size it is.
  const wide = await page.locator(".part.sidebar").boundingBox();
  await shot("screenshot-board", { x: 0, y: 0, width: Math.round(wide.x + wide.width + 8), height: 300 });

  await page.locator('.editor-actions [aria-label="Review Changes"]').click();
  await pause(4000);
  // Collapse the spec: the shot is about the code and its findings.
  const md = page.locator(".multiDiffEntry .header", { hasText: ".md" });
  for (let i = 0; i < (await md.count()); i++) await md.nth(i).locator(".codicon-chevron-down").first().click().catch(() => {});
  await pause(1500);
  await page.locator(".view-line", { hasText: "hit = self._entries.get(key)" }).last().click();
  await palette("View: Focus Problems");
  await pause(1500);
  await shot("screenshot-review", { x: 0, y: 0, width: 1440, height: 760 });

  await palette("View: Close Panel");
  await palette("View: Close All Editors");
  await palette("File: Open File...");
  await page.keyboard.press(process.platform === "darwin" ? "Meta+A" : "Control+A");
  await page.keyboard.type(join(home, "config", "policy.yaml"));
  await page.keyboard.press("Enter");
  await pause(2500);
  await page.keyboard.press(process.platform === "darwin" ? "Meta+F" : "Control+F");
  await page.keyboard.type("important]");
  await page.keyboard.press("Escape");
  await page.keyboard.type("urgent]");
  await pause(3000);
  // The mouse on the flagged word opens its hover; the palette's Show or Focus Hover did not on xvfb.
  // By position: the squiggle sits under the text layer, which takes the pointer events.
  const squiggle = await page.locator(".part.editor .squiggly-error").last().boundingBox();
  await page.mouse.move(squiggle.x + squiggle.width / 2, squiggle.y + squiggle.height / 2, { steps: 4 });
  await page.locator(".monaco-hover", { hasText: "unknown severity" }).first().waitFor({ state: "visible", timeout: 10_000 });
  await pause(800);
  // The hover and the line it is about, across the editor: wherever the hover opened, above or below.
  const editor = await page.locator(".part.editor").boundingBox();
  const hover = await page.locator(".monaco-hover", { hasText: "unknown severity" }).first().boundingBox();
  const cursor = await page.locator(".part.editor .cursor").first().boundingBox();
  const top = Math.min(hover.y, cursor.y) - 12;
  const bottom = Math.max(hover.y + hover.height, cursor.y + cursor.height) + 12;
  await shot("screenshot-config", { x: editor.x, y: Math.round(top), width: Math.round(editor.width), height: Math.round(bottom - top) });
}

function SETTINGS(port) {
  return {
    "kraft.url": `http://127.0.0.1:${port}`,
    "kraft.notifications": "gates",
    "workbench.colorTheme": "Default Dark Modern",
    "workbench.startupEditor": "none",
    "workbench.tips.enabled": false,
    "workbench.layoutControl.enabled": false,
    "window.titleBarStyle": "custom",
    "window.menuStyle": "custom",
    "window.commandCenter": false,
    "window.restoreWindows": "none",
    "security.workspace.trust.enabled": false,
    "telemetry.telemetryLevel": "off",
    "update.mode": "none",
    "extensions.ignoreRecommendations": true,
    "chat.commandCenter.enabled": false,
    "chat.disableAIFeatures": true,
    "workbench.secondarySideBar.defaultVisibility": "hidden",
    "editor.minimap.enabled": false,
    "git.enabled": false,
    "editor.fontSize": 13,
    "diffEditor.hideUnchangedRegions.enabled": false,
    // Inline, as a narrow window shows it: a new file side by side is half empty.
    "diffEditor.renderSideBySide": false,
    "files.simpleDialog.enable": true,
    "files.hotExit": "off",
  };
}
