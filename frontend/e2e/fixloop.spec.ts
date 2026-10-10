import { createItem, expect, status, test } from "./fixtures";
import type { Page } from "@playwright/test";
import { scaledTimeout } from "../e2e-timing";

// The node canvas on a verification that went three rounds: the round menu, the repair and judge on the
// arc, and the changed-test-scope task opening into its repositories and scopes. The fixture server's
// library turns every builtin into an inert `true` (tests/support/harness.py), so a real run can never
// produce scope runs or a second round. This spec therefore opens a real item and answers only its own
// detail request with the payload such a run would leave; everything else is the real server and the
// real browser, which is what these checks are for: layout, the animation, the keys, the camera.

const SCOPE = "verification.tests.test_changed_scopes";
const WORKSPACE = { kind: "workspace", root: "ws", mounts: { pkg: { repository: "pkg", path: "repos/pkg" } } };
const FROZEN = JSON.stringify({
  chain: { nodes: [{ id: "verification", kind: "exec", fix_loop: { max_attempts: 2, tasks: [{ id: "repair", kind: "agent" }], judge: { id: "judge", kind: "agent" } }, steps: [
    { id: "checks", tasks: [{ id: "lint", kind: "subprocess" }] },
    { id: "tests", tasks: [{ id: "test_changed_scopes", kind: "builtin", ref: "kraft.verify_changed_test_scopes" }] },
  ] }] },
  target: WORKSPACE,
});

let n = 0;
const session = (hook_point: string, round: number, status = "done", over: Record<string, unknown> = {}) => ({
  id: `e2e-${n++}`, node_id: "verification", hook_point, status, attempt: 1, thread: 1, round, wall_ms: 24_000, model: null,
  created_at: `2026-09-13T09:${String(10 + n).padStart(2, "0")}:00Z`, started_at: "2026-09-13T09:00:00Z", exited_at: null, ...over,
});
/** A scope run and the session that ran it. */
const run = (repository: string, command: string, round: number, status: string, order: number) => {
  const s = session(SCOPE, round, status, { command, repository });
  return { s, r: { session_id: s.id, node_id: "verification", hook_point: SCOPE, repository, round, command, passed: status === "done", scope: `${command.split("-")[1]}/**`, order } };
};

/** Round 1 failed in pkg, round 2 failed in ws, round 3 (now) ran two scopes in ws and dropped one. */
function sessions() {
  const scopes = [
    run("ws", "just test-a", 0, "done", 0), run("ws", "just test-d", 0, "done", 1), run("pkg", "just test-b", 0, "failed", 1),
    run("ws", "just test-a", 1, "failed", 0),
    run("ws", "just test-a", 2, "done", 0), run("ws", "just test-c", 2, "done", 2),
  ];
  const rest = [
    ...[0, 1, 2].map((r) => session("verification.checks.lint", r)),
    session("verification.fix_loop.main.repair", 1, "done", { wall_ms: 310_000, model: "sonnet" }),
    session("verification.fix_loop.main.repair", 2, "done", { wall_ms: 290_000, model: "sonnet" }),
    session("verification.fix_loop.judge", 1, "done", { wall_ms: 31_000, model: "sonnet" }),
  ];
  // In the order a round really goes: the repair into it, its lint, its scopes, then the judge after it.
  const all = [...rest, ...scopes.map((x) => x.s)];
  const step = (h: string) => (h.includes("repair") ? 0 : h.includes("lint") ? 1 : h.includes("judge") ? 3 : 2);
  for (const x of all) x.created_at = `2026-09-13T09:${String(10 + x.round * 5 + step(x.hook_point)).padStart(2, "0")}:00Z`;
  return { worker_sessions: all, scope_runs: scopes.map((x) => x.r) };
}

/** One finished item serves both tests: the server runs one at a time, and a second item is only a second wait. */
let finished: Promise<string> | undefined;
const finishedItem = (page: Page) =>
  (finished ??= (async () => {
    const id = await createItem(page, `fix loop canvas ${Date.now()}`, "quick-task");
    await page.goto(`/work-items/${id}`);
    await expect(status(page, "DONE")).toBeVisible({ timeout: scaledTimeout(100_000) });
    return id;
  })());

/** Opens that item's verification as if it had run three rounds. */
async function openVerification(page: Page) {
  const id = await finishedItem(page);
  const real = await (await page.request.get(`/api/work-items/${id}`)).json();
  const verification = { id: "verification", kind: "exec", gate_after: null, fix_loop: "verification.fix_loop", tasks: ["verification.checks.lint", SCOPE], steps: [["verification.checks.lint"], [SCOPE]] };
  const shown = { ...real, current_node_id: "verification", display_status: "running", chain_definition: { template_id: "default", nodes: [verification] }, materialized_chain: FROZEN, repo: "/code/kraft-web", ...sessions() };
  await page.route(new RegExp(`/api/work-items/${id}(\\?.*)?$`), (route) => route.fulfill({ json: shown }));
  await page.goto(`/work-items/${id}/nodes/verification`);
  const canvas = page.getByRole("group", { name: "verification" });
  await expect(canvas.getByRole("button", { name: /^test_changed_scopes/ })).toBeVisible();
  return { canvas, id };
}
const frame = (page: Page) => page.getByRole("group", { name: /repositories and scopes/ });
/** The frame at its full size: as wide as its contents, which are laid out at that width from the start. */
const full = async (page: Page) => {
  const width = await frame(page).locator(".scope-inner").evaluate((el) => getComputedStyle(el).width);
  expect(parseFloat(width)).toBeGreaterThan(300);
  await expect(frame(page)).toHaveCSS("width", width);
};

test("a verification that went three rounds: open the scopes, pick one, step back, and move between rounds", async ({ page }) => {
  const { canvas } = await openVerification(page);

  // The repair and judge are on the arc, and the round sits beside the zoom.
  await expect(canvas.getByRole("button", { name: /^repair, fix-loop repair/ })).toBeVisible();
  await expect(canvas.getByRole("button", { name: /^judge, fix-loop judge/ })).toBeVisible();
  await expect(canvas.getByRole("button", { name: "round 3 of 3 · latest" })).toBeVisible();

  // Selecting the task opens it: at its full width, once the move is over.
  await canvas.getByRole("button", { name: /^test_changed_scopes/ }).click();
  await full(page);
  await expect(page.locator(".canvas-world.is-glide")).toHaveCount(0);
  await expect(frame(page).locator(".scope-row")).toHaveCount(2);
  // The box it replaced held the focus; the frame's title has it now.
  await expect(frame(page).getByRole("button", { name: "test_changed_scopes" })).toBeFocused();

  // A chip opens its scope's pane, with the scope in the address.
  await frame(page).getByRole("button", { name: /^just test-c, done, new this round/ }).click();
  await expect(page).toHaveURL(/scope=ws%3Ajust\+test-c/);
  const pane = page.getByRole("complementary", { name: "just test-c pane" });
  await expect(pane).toContainText("test scope · round 3 of 3 · passed 24s");
  await expect(pane).toContainText("c/**");

  // Esc steps back from the scope to the task, then closes the frame, leaving the task's pane.
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");
  await expect(page).not.toHaveURL(/scope=/);
  await expect(frame(page)).toHaveCount(0);
  await expect(page.getByRole("complementary", { name: "test_changed_scopes pane" })).toBeVisible();

  // Round 1 from the menu, which opens upward; `latest ↩` comes back.
  await page.getByRole("button", { name: "round 3 of 3 · latest" }).click();
  const menu = page.getByRole("menu", { name: "Fix loop rounds" });
  await expect(menu.getByRole("menuitemradio")).toHaveText([/Round 3 · now/, /Round 2/, /Round 1/]);
  await menu.getByRole("menuitemradio", { name: /^Round 1/ }).click();
  await expect(page.getByRole("button", { name: "round 1 of 3" })).toBeVisible();
  await expect(canvas.getByRole("button", { name: /^judge, fix-loop judge agent task, not started/ })).toBeVisible();
  await page.getByRole("button", { name: "latest ↩" }).click();
  await expect(page.getByRole("button", { name: "round 3 of 3 · latest" })).toBeVisible();
  await expect(page.getByRole("button", { name: "latest ↩" })).toHaveCount(0);
});

test("the frame moves while it opens, and does not for someone who asked for less motion", async ({ page }) => {
  const { canvas } = await openVerification(page);
  await canvas.getByRole("button", { name: /^test_changed_scopes/ }).click();
  // Everything glides for the 420ms the frame grows in.
  await expect(page.locator(".canvas-world.is-glide")).toHaveCount(1);
  await full(page);
  await expect(page.locator(".canvas-world.is-glide")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(frame(page)).toHaveCount(0);

  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.locator(".canvas.is-calm")).toHaveCount(1);
  await canvas.getByRole("button", { name: /^test_changed_scopes/ }).click();
  // At full size at once, nothing glides, and the frame only fades in.
  await full(page);
  await expect(page.locator(".canvas-world.is-glide")).toHaveCount(0);
  await expect(frame(page)).toHaveCSS("animation-name", "scope-in");
  await expect(frame(page)).toHaveCSS("animation-duration", "0.12s");
  await page.keyboard.press("Escape");
  await expect(frame(page)).toHaveCount(0);
});
