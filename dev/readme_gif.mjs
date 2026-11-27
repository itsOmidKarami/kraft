#!/usr/bin/env node
// Record the README's loop: one work item filed, stopped at each gate for a
// person, and back at the final review with its draft pull request, in the
// real web UI of a dev instance. dev/readme_gif.py stages the instance and
// runs this; `just readme-gif` runs both.
//
// Agents are the fake one. It writes "fake spec body" for every document, so
// the gate documents are swapped for readable ones at the API response, as
// the console screenshot recipe does; they describe what the fake agent
// really changes (calc.py's a - b to a + b). The dev build's version and
// update notice are hidden the same way. While agents run, the loop plays at
// FAST and a badge on screen says so; clicks and reading play at real speed.
//
//   node dev/readme_gif.mjs [--out DIR]   (run from frontend/, for its Playwright)
//
// Writes DIR/run.gif and DIR/run.mp4, default .github/assets. CHROMIUM=PATH
// launches that browser instead of the one `npx playwright install` fetched.

import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { chromium } = createRequire(join(ROOT, "frontend", "package.json"))("playwright");
const API = `http://127.0.0.1:${process.env.KRAFT_PORT ?? "8766"}/api`;
const UI = process.env.KRAFT_UI ?? "http://localhost:5173";
const outArg = process.argv.indexOf("--out");
const OUT = resolve(outArg > 0 ? process.argv[outArg + 1] : join(ROOT, ".github", "assets"));
// The web UI's desktop layout, sidebar and all. GitHub draws the GIF at its
// README column's width (about 830-900 px), so a wider GIF stays sharp on a
// high-density screen.
const W = 1280;
const H = 800;
const FAST = 6;
const TITLE = "Fix the sign bug in add()";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const api = async (path) => (await fetch(API + path)).json();
const gateOf = (it) => (it.pending_gate && typeof it.pending_gate === "object" ? it.pending_gate.id : it.pending_gate);

async function until(ok, what, ms = 90_000) {
  for (const end = Date.now() + ms; Date.now() < end; await sleep(100)) {
    const got = await ok();
    if (got) return got;
  }
  throw new Error(`never ${what}`);
}

// What each gate shows, in place of the fake agent's one-line bodies.
const DOCS = {
  spec: ["Fix the sign bug in add()", `
## Problem

\`add(2, 3)\` returns \`-1\`: \`calc.add\` subtracts its arguments, and
\`test_add\` expects \`5\`.

## Change

\`add\` returns \`a + b\`. Nothing else calls it, so nothing else changes.

## Done when

- \`test_add\` passes
- the whole suite passes on the branch
`],
  plan: ["Plan: fix the sign bug in add()", `
### Task 1: Return a + b from add()

1. In \`calc.py\`, change \`return a - b\` to \`return a + b\`.
2. Run \`uv run pytest -q\` and expect \`1 passed\`.
3. Commit: \`fix: add() adds\`.
`],
  work_brief: ["Work brief", `
## What changed

\`calc.add\` returns \`a + b\` instead of \`a - b\`. One file, one line.

## Checks

- \`uv run pytest -q\`: 1 passed
- the spec's done-when list: met

Approving opens a draft pull request.
`],
  review_brief: ["Final review", `
Draft pull request !1 is open: \`add()\` now adds, one line in \`calc.py\`,
and the tests passed on the branch. Approving marks it ready for review.
`],
};
const GATE_DOC = { spec_approval: "spec", plan_approval: "plan", local_review: "work_brief", final_review: "review_brief" };

// A pointer the viewer can follow (headless recording draws none), and the
// badge that says when the loop is fast-forwarding.
function overlay() {
  addEventListener("DOMContentLoaded", () => {
    const c = document.createElement("div");
    c.innerHTML = `<svg width="22" height="22" viewBox="0 0 24 24"><path d="M4 2l15 10-7 1.5L8.5 21z" fill="#fff" stroke="#000" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
    Object.assign(c.style, { position: "fixed", left: "0", top: "0", zIndex: 2147483647, pointerEvents: "none", transform: "translate(-100px,-100px)" });
    const badge = document.createElement("div");
    badge.id = "readme-gif-fast";
    Object.assign(badge.style, { position: "fixed", right: "16px", bottom: "16px", zIndex: 2147483646, pointerEvents: "none", display: "none", padding: "4px 10px", borderRadius: "6px", font: "600 13px system-ui, sans-serif", color: "#111", background: "rgba(255,255,255,.85)" });
    document.body.append(c, badge);
    let at = "";
    addEventListener("mousemove", (e) => { at = `translate(${e.clientX - 4}px,${e.clientY - 2}px)`; c.style.transform = at; }, true);
    addEventListener("mousedown", () => { c.style.transform = `${at} scale(0.8)`; }, true);
    addEventListener("mouseup", () => { c.style.transform = at; }, true);
  });
}

async function record() {
  const latest = (await api("/update")).latest?.replace(/^v/, "");
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 2 });
  await ctx.addInitScript(overlay);
  await ctx.route("**/api/update", (r) => r.fulfill({ json: { behind: false, pending: { release: null, restart: [] } } }));
  await ctx.route("**/api/health", async (r) => {
    const res = await r.fetch();
    r.fulfill({ response: res, json: { ...(await res.json()), ...(latest && { version: latest, installed: latest }) } });
  });
  await ctx.route("**/api/work-items/*/artifact*", async (r) => {
    const res = await r.fetch();
    const body = await res.json();
    const kind = body.path?.split("/").pop()?.replace(/\.md$/, "");
    if (DOCS[kind]) [body.title, body.content] = DOCS[kind];
    r.fulfill({ response: res, json: body });
  });

  const page = await ctx.newPage();
  // Chrome's screencast, not Playwright's video: lossless frames, so small
  // text survives the scale down. A frame comes only when the page repaints.
  const frames = [];
  const cdp = await ctx.newCDPSession(page);
  cdp.on("Page.screencastFrame", (f) => {
    frames.push({ t: f.metadata.timestamp, png: Buffer.from(f.data, "base64") });
    cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }).catch(() => {});
  });
  // When the loop switches between real speed and FAST, in screencast time.
  const speeds = [];
  async function fast(on) {
    await page.evaluate(([on, label]) => {
      const b = document.getElementById("readme-gif-fast");
      b.textContent = label;
      b.style.display = on ? "block" : "none";
    }, [on, `agents working · ${FAST}× speed`]);
    await sleep(60);
    speeds.push({ t: Date.now() / 1000, speed: on ? FAST : 1 });
  }

  let mouse = { x: W / 2, y: H / 2 };
  async function click(loc) {
    await loc.waitFor();
    const b = await loc.boundingBox();
    const to = { x: b.x + b.width / 2, y: b.y + b.height / 2 };
    await page.mouse.move(to.x, to.y, { steps: Math.max(10, Math.round(Math.hypot(to.x - mouse.x, to.y - mouse.y) / 25)) });
    mouse = to;
    await sleep(200);
    await page.mouse.down();
    await sleep(80);
    await page.mouse.up();
  }
  // Agents run at FAST until the item stops at `g`, then the gate opens at real speed.
  async function gate(id, g, read) {
    await fast(true);
    await until(async () => gateOf(await api(`/work-items/${id}`)) === g, `at ${g}`);
    const review = page.getByRole("button", { name: "Review", exact: true }).last();
    await review.waitFor();
    await sleep(300);
    await fast(false);
    await sleep(500);
    await click(review);
    await page.getByText(DOCS[GATE_DOC[g]][0], { exact: true }).first().waitFor();
    await sleep(read);
  }
  const approve = () => click(page.getByRole("button", { name: "Approve", exact: true }).first());

  await page.goto(UI);
  await page.getByText("Add divide()").waitFor();
  await page.mouse.move(mouse.x, mouse.y);
  await cdp.send("Page.startScreencast", { format: "png", maxWidth: W * 2, maxHeight: H * 2 });
  await sleep(800);
  await click(page.getByRole("button", { name: /New work item/ }));
  await page.getByLabel("Title").pressSequentially(TITLE, { delay: 28 });
  await sleep(250);
  await page.getByLabel("Brief").pressSequentially("add(2, 3) returns -1", { delay: 25 });
  await sleep(400);
  await click(page.getByRole("button", { name: "More ways to create" }));
  await sleep(300);
  await click(page.getByRole("menuitem", { name: "Create and start" }));
  const id = await until(async () => (await api("/work-items")).items.find((x) => x.title === TITLE)?.id, "filed");

  await gate(id, "spec_approval", 2200);
  await approve();
  await gate(id, "plan_approval", 1000);
  await approve();
  await gate(id, "local_review", 1000);
  await approve();
  await gate(id, "final_review", 0);
  // Point at the pull request's link in the breadcrumb, without covering it.
  const pr = page.getByRole("link", { name: /!\d/ }).first();
  await pr.waitFor();
  const b = await pr.boundingBox();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height + 14, { steps: 20 });
  await sleep(2600);
  await cdp.send("Page.stopScreencast");
  // A still page sends no frames: the last one lasts until now, the hold above.
  frames.push({ t: Date.now() / 1000, png: frames.at(-1).png });
  await browser.close();
  return { frames, speeds };
}

function encode({ frames, speeds }) {
  const speedAt = (t) => speeds.findLast((s) => s.t <= t)?.speed ?? 1;
  const dir = mkdtempSync(join(tmpdir(), "readme-gif-"));
  let seconds = 0;
  const list = frames.slice(0, -1).map((f, i) => {
    writeFileSync(join(dir, `${i}.png`), f.png);
    const d = (frames[i + 1].t - f.t) / speedAt(f.t);
    seconds += d;
    return `file '${i}.png'\nduration ${d.toFixed(3)}`;
  });
  writeFileSync(join(dir, "frames.txt"), `${list.join("\n")}\nfile '${list.length - 1}.png'\n`);
  const ff = (...args) => execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "concat", "-i", join(dir, "frames.txt"), ...args], { stdio: "inherit" });
  ff("-vf", "fps=30,scale=1600:-2:flags=lanczos", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", "-movflags", "+faststart", join(OUT, "run.mp4"));
  ff("-vf", `fps=12,scale=${W}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle`, "-loop", "0", join(OUT, "run.gif"));
  rmSync(dir, { recursive: true });
  return seconds.toFixed(1);
}

const seconds = encode(await record());
console.log(`readme-gif: ${join(OUT, "run.gif")} and run.mp4, ${seconds} s`);
