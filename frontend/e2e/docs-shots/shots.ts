import { errors, expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";

/**
 * The docs screenshots taken from the dashboard demo, one test per picture. Each writes
 * `.github/assets/<name>.png` (SHOTS_OUT moves them). The demo's sample data is fixed, so a retake
 * changes a picture only where the screen changed. frontend/README.md lists where each one is shown.
 */

const OUT = process.env.SHOTS_OUT
  ? path.resolve(process.env.SHOTS_OUT)
  : path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../.github/assets");

/** Writes `<name>.png` unless the picture there already looks the same (pixelmatch at its default 0.1): the bytes,
 *  and a few anti-aliased pixels, vary run to run, and a retake should change only the pictures whose screen changed. */
const save = (name: string, png: Buffer) => {
  const file = `${OUT}/${name}.png`;
  if (existsSync(file)) {
    const [a, b] = [PNG.sync.read(readFileSync(file)), PNG.sync.read(png)];
    if (a.width === b.width && a.height === b.height && pixelmatch(a.data, b.data, undefined, a.width, a.height, { threshold: 0.1 }) === 0) return;
  }
  writeFileSync(file, png);
};

/**
 * The demo as a visitor never sees it: without its DEMO pill and things-to-try list, and with its
 * live motion paused, so no row moves while the picture is taken. Motion pauses while the tab is
 * hidden; the tab reads hidden while the demo boots and shown after, as the smoke suite does it.
 */
const still = (page: Page) =>
  page.addInitScript(() => {
    try { sessionStorage.setItem("kraft.demo.badge.hidden", "1"); } catch { /* no storage: the pill shows */ }
    let booting = true;
    const real = (name: "hidden" | "visibilityState") => Object.getOwnPropertyDescriptor(Document.prototype, name)!.get!;
    const [hidden, state] = [real("hidden"), real("visibilityState")];
    Object.defineProperty(Document.prototype, "hidden", { configurable: true, get() { return booting || hidden.call(this); } });
    Object.defineProperty(Document.prototype, "visibilityState", { configurable: true, get() { return booting ? "hidden" : state.call(this); } });
    document.addEventListener("DOMContentLoaded", () => { booting = false; });
  });

test.beforeEach(async ({ page }) => still(page));

/** Lets every animation that ends (a selection ring, a panel sliding in) end first, so two retakes of an
 *  unchanged screen match; endless ones (a running item's pulse) and paused ones are left as they are. */
const settle = (page: Page) =>
  page
    .waitForFunction(
      // None running that ends: a paused one is left as it is, an endless one never ends. Polled, so one that
      // starts as another ends is waited on too, and one paused partway stops counting.
      () => !document.getAnimations().some((a) => a.playState === "running" && a.effect?.getTiming().iterations !== Infinity),
      undefined,
      { timeout: 5_000 },
    )
    // Still moving after 5s (a long or restarting animation): take the picture anyway, and say so. Anything
    // else (the page navigated, or closed) fails the shot, so a half-loaded frame never replaces a picture.
    .catch((e: unknown) => {
      if (!(e instanceof errors.TimeoutError)) throw e;
      console.warn(`docs-shots: ${page.url()} still animating after 5s; captured as it is`);
    });

const board = async (page: Page) => {
  await page.goto("./");
  await expect(page.getByRole("heading", { name: "Board", level: 1 })).toBeVisible();
};

/** The page right of the sidebar, its top bar included, down to `bottom` (the viewport's bottom by
 *  default): the sidebar adds nothing to a picture of one screen, and its footer names the build. */
const shootMain = async (page: Page, name: string, bottom?: number) => {
  await page.mouse.move(0, 0);
  await page.waitForLoadState("networkidle");
  await settle(page);
  const main = (await page.locator("#ng-main").boundingBox())!;
  const view = page.viewportSize()!;
  const height = Math.min(bottom ?? view.height, view.height);
  save(name, await page.screenshot({ clip: { x: main.x, y: 0, width: view.width - main.x, height } }));
};

test("stops: the board's Needs you group, one row per kind of stop", async ({ page }) => {
  await board(page);
  const group = page.getByRole("region", { name: "Needs you" });
  await expect(group.getByText("Running time hit its 8h cap")).toBeVisible();
  await page.mouse.move(0, 0);
  await settle(page);
  save("stops", await group.screenshot());
});

test("stop-peek: a stopped item's side panel, with its reason and what to do", async ({ page }) => {
  await board(page);
  await page.getByText("Fix flaky retry test in the worker pool").click();
  await expect(page.getByRole("button", { name: "Open item" })).toBeVisible();
  await shootMain(page, "stop-peek");
});

test("composer: the board's composer, filled in", async ({ page }) => {
  await board(page);
  await page.getByRole("button", { name: "New work item" }).click();
  const composer = page.getByRole("region", { name: "New work item" });
  await composer.getByRole("textbox", { name: "Title" }).fill("Cache the schema lookups");
  await composer.getByRole("textbox", { name: "Brief" }).fill("Lookups hit the registry on every request. Cache them per process, with a TTL.");
  // Typed, not being typed: no focus ring on the brief.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  const box = (await composer.boundingBox())!;
  await shootMain(page, "composer", box.y + box.height + 12);
});

test("storage: Settings > Storage, its usage and where it goes", async ({ page }) => {
  await page.goto("./settings/storage");
  await expect(page.getByText("Where it goes")).toBeVisible();
  // Down to the Worktrees list's first row, so no row is cut in half.
  const row = (await page.getByRole("row").nth(1).boundingBox())!;
  await shootMain(page, "storage", row.y + row.height);
});

/** The board's one item at a gate, by its Review button: the demo's ids are its own business. */
const gateItem = async (page: Page) => {
  await board(page);
  await page.getByRole("button", { name: /^Review/ }).first().click();
  await expect(page).toHaveURL(/\/work-items\/[0-9a-f]+\/review/);
  return new URL(page.url()).pathname.match(/work-items\/([0-9a-f]+)/)![1];
};

const shoot = async (page: Page, name: string) => {
  await page.mouse.move(0, 0);
  await page.waitForLoadState("networkidle");
  await settle(page);
  save(name, await page.screenshot()); // the viewport, not the full page
};

test("board: the board, grouped by status", async ({ page }) => {
  await board(page);
  await shoot(page, "board");
});

test("search: Ctrl/Cmd+K with a query, over the board", async ({ page }) => {
  await board(page);
  await page.keyboard.press("Control+k");
  await page.keyboard.type("caching");
  await page.waitForTimeout(1000); // the results are debounced
  await shoot(page, "search");
});

test("gate: the review page of the item at a gate, its document open", async ({ page }) => {
  await gateItem(page);
  await expect(page.getByRole("button", { name: "Approve", exact: true }).first()).toBeVisible();
  await shoot(page, "gate");
});

test("item: the same item's page, its gate selected", async ({ page }) => {
  const id = await gateItem(page);
  await page.goto(`./work-items/${id}?sel=final_review`);
  await expect(page.getByText("Waiting for you to approve final review.")).toBeVisible();
  await shoot(page, "item");
});

test("review: a must-fix comment being written on a changed line", async ({ page }) => {
  const id = await gateItem(page);
  await page.goto(`./work-items/${id}/review`);
  // The contract suite's range pick (e2e/contract/review.spec.ts): the removed line to its replacement.
  const f = page.locator("[data-file]").first();
  const pick = (side: string, n: number) => f.getByRole("button", { name: `Pick ${side} line ${n}`, exact: true }).first();
  await pick("old", 2).click();
  await pick("new", 2).click({ modifiers: ["Shift"] });
  await pick("new", 2).hover();
  await page.locator(".rv-plus:visible").first().click();
  await page.getByRole("radio", { name: "Must fix", exact: true }).click();
  await page.getByRole("textbox", { name: "Comment" }).fill("Sequence is imported but nothing uses it yet. Drop it, or type the reports with it.");
  await shoot(page, "review");
});

test("analytics: the Analytics page", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 660 });
  await page.goto("./analytics");
  await expect(page.getByText("Waiting at each gate")).toBeVisible();
  await shoot(page, "analytics");
});

test("chains: Templates > Chains on the default chain, a gate selected", async ({ page }) => {
  await page.goto("./templates/chains/default");
  await page.getByText("spec_approval", { exact: true }).first().click();
  await shoot(page, "chains");
});

test("policy: Settings > Policy on its Limits tab", async ({ page }) => {
  await page.goto("./settings/policy/limits");
  await expect(page.getByRole("heading", { name: "Policy", level: 1 })).toBeVisible();
  await shoot(page, "policy");
});

test("setup: the setup wizard, on a home with no repo and no item", async ({ page }) => {
  // The demo has repos and items; this one picture answers both lists empty, as a new home does.
  // The demo's fetch shim takes the place of window.fetch at boot: wrap whatever it sets.
  await page.addInitScript(() => {
    const native = window.fetch.bind(window);
    let inner: typeof fetch = native;
    const empty = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input instanceof Request ? input.url : String(input), location.href);
      if (!url.pathname.startsWith("/api/")) return native(input, init);
      const res = await inner(input, init);
      const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
      const list = url.pathname === "/api/repos" ? "repos" : url.pathname === "/api/work-items" && url.searchParams.get("archived") !== "true" ? "items" : null;
      if (method !== "GET" || !list) return res;
      return new Response(JSON.stringify({ ...(await res.json()), [list]: [] }), { status: res.status, headers: res.headers });
    };
    Object.defineProperty(window, "fetch", { configurable: true, get: () => empty, set: (f: typeof fetch) => { inner = f; } });
  });
  await page.goto("./");
  await expect(page.getByText("Nothing on the board yet")).toBeVisible();
  await shoot(page, "setup");
});

test.describe("phone", () => {
  // The phone's viewport and touch over the project's own options (locale, time zone, timeouts).
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("mobile: the board in the phone layout", async ({ page }) => {
    await page.goto("./");
    await page.waitForTimeout(1500);
    await shoot(page, "mobile");
  });
});

test("chain-extends: a chain that extends default, an inherited node it overrides selected", async ({ page }) => {
  await page.goto("./templates/chains/team-default");
  await expect(page.getByText("extends default", { exact: true })).toBeVisible();
  // Pan the canvas so the node it overrides and the one it adds are in view.
  const node = page.getByText("verification", { exact: true }).first();
  const at = (await node.boundingBox())!;
  await page.mouse.move(900, 250);
  await page.mouse.down();
  await page.mouse.move(900 - (at.x - 330), 250, { steps: 12 });
  await page.mouse.up();
  await node.click();
  await expect(page.getByText(/^Inherited from default\./)).toBeVisible();
  await shootMain(page, "chain-extends");
});

test("fixloop: a running item's verification node on its second fix-loop round", async ({ page }) => {
  await board(page);
  await page.getByText("Bump the VS Code extension to schema v4", { exact: true }).first().click();
  await page.getByRole("button", { name: "Open item" }).click();
  await expect(page).toHaveURL(/\/work-items\/[0-9a-f]+$/);
  await page.goto(`${new URL(page.url()).pathname.replace(/^\/demo\//, "./")}?sel=verification`);
  await expect(page.getByText("round 2").first()).toBeVisible();
  // The node's own view, where the loop's rounds, repair and judge are drawn.
  await page.getByRole("button", { name: /^Focus/ }).click();
  await page.waitForTimeout(800);
  await shootMain(page, "fixloop");
});

test("new-item-page: the full new-item page, a title typed", async ({ page }) => {
  await board(page);
  await page.getByRole("button", { name: "New work item" }).click();
  await page.getByText("More options").click();
  await expect(page).toHaveURL(/\/work-items\/new$/);
  await page.getByRole("textbox", { name: "Title" }).fill("Cache the schema lookups");
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  // Down to the chain's panel, not the empty page under it.
  const fit = (await page.getByRole("button", { name: "Fit" }).boundingBox())!;
  await shootMain(page, "new-item-page", fit.y + fit.height + 24);
});

for (const [name, path, ready] of [
  ["harnesses", "settings/harnesses", "every harness · click one, or a lane"],
  ["notifications", "settings/notifications", "A notification looks like"],
  ["access", "settings/access", "Allowed hosts"],
  ["appearance", "settings/appearance", "Colour amount"],
] as const) {
  test(`${name}: Settings > ${path.split("/")[1]}`, async ({ page }) => {
    await page.goto(`./${path}`);
    await expect(page.getByText(ready, { exact: true }).first()).toBeVisible();
    await shootMain(page, name);
  });
}
