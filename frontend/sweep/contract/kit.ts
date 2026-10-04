import { test, type Page } from "@playwright/test";
import { buildScenario, type Scenario, type Variant } from "../fixtures";
import { installMocks, type MockOptions } from "../mockApi";
import { NG_NOW } from "../ngItems";

/**
 * What the UI contract specs share: the app on its mocked API, the few helpers every row uses, and the
 * runner. The contract is `Row[]`: one plain-language claim per row, run on the built SPA with
 * `/api/**` mocked from sweep/fixtures.ts. `just ui-contract` runs it; sweep/README.md says what it is for.
 */

export interface AppOpts {
  data?: Variant;
  mock?: MockOptions;
  side?: "pinned" | "rail";
  /** Extra routes, registered after the mock's so they win (Playwright tries the last route first). */
  routes?: (p: Page) => Promise<unknown>;
  /** Wait for this text or role before settling (a page whose main has no h1, a dialog). */
  ready?: (p: Page) => Promise<unknown>;
  /** No desktop sidebar to wait for: the phone. */
  noShell?: boolean;
  /** Seed the scenario (a seed from sweep/fixtures.ts) before the mocks read it. */
  tweak?: (S: Scenario) => void;
}

/** GET /editors, which the mock does not answer: two installed editors plus the system opener, VS Code the default. */
export const editorsRoute = (p: Page) => p.route("**/api/editors", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ available: ["code", "cursor"], system: true, default: "code" }) }));

/** The app on its mocked API at `url(S)`, the clock at NG_NOW. Returns the scenario so a caller can name ids. */
export async function app(p: Page, url: string | ((S: Scenario) => string), o: AppOpts = {}): Promise<Scenario> {
  const S = buildScenario(o.data ?? "default");
  o.tweak?.(S);
  await installMocks(p, S, { ngBoard: true, areas: "default", ...o.mock });
  if (o.routes) await o.routes(p);
  if (o.side) await p.addInitScript((v) => localStorage.setItem("kraft.sidebar.v2", v), o.side);
  await p.clock.setFixedTime(new Date(NG_NOW));
  await p.goto(typeof url === "string" ? url : url(S));
  if (!o.noShell) await p.locator(".ng-sidebar").waitFor({ timeout: 10_000 });
  if (o.ready) await o.ready(p);
  else await p.locator("main h1").first().waitFor({ timeout: 8000 }).catch(() => {});
  await p.waitForTimeout(700);
  return S;
}

/** The URL of one of the ng scenarios' work items (`running`, `needs-gate`, `failed`, `capped`…), with a path after it. */
export const item = (sc: string, tail = "") => (S: Scenario) => `/work-items/${S.ng[sc]}${tail}`;
export const pause = (p: Page, ms = 500) => p.waitForTimeout(ms);
/** Move the pointer off the sidebar and let its 180ms leave timer run. */
export const away = async (p: Page) => { await p.mouse.move(700, 420); await pause(p, 600); };
/** The name of what holds focus: its aria-label, else its text. */
export const focusedName = (p: Page) => p.evaluate(() => (document.activeElement?.getAttribute("aria-label") || document.activeElement?.textContent || document.activeElement?.tagName || "").trim().slice(0, 60));

/** A claim and the steps that check it; a step that throws fails the row. */
export interface Row { name: string; run: (p: Page) => Promise<void> }

/** One test per row, on a 1280×800 window unless the row resizes it. */
export function contract(rows: Row[]) {
  for (const r of rows) {
    test(r.name, async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 800 });
      await r.run(page);
    });
  }
}

/** Drag the pane's left edge 80px wider, reload, and return the edge's x at each point. */
export async function dragPaneEdge(p: Page, handle: ReturnType<Page["locator"]>, reload: () => Promise<void>) {
  const b0 = (await handle.boundingBox())!;
  await p.mouse.move(b0.x + b0.width / 2, b0.y + b0.height / 2);
  await p.mouse.down();
  await p.mouse.move(b0.x - 80, b0.y + b0.height / 2, { steps: 8 });
  await p.mouse.up();
  await pause(p, 300);
  const b1 = (await handle.boundingBox())!;
  await reload();
  const b2 = (await handle.boundingBox())!;
  return [b0.x, b1.x, b2.x];
}
