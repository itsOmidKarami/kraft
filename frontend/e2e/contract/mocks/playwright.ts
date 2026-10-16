import type { Page } from "@playwright/test";
import type { Scenario } from "./fixtures";
import { NG_NOW } from "./ngItems";
import { ABORT, HANG, mockServer, type MockOptions } from "./mockApi";

export type { MockOptions } from "./mockApi";

/**
 * Serve the whole /api surface from the scenario on Playwright's routes: the
 * answers are mockApi.ts's, this puts them on the wire.
 */
export async function installMocks(page: Page, S: Scenario, opts: MockOptions = {}) {
  // Every page is mocked here, before it navigates: fixed time keeps the app's relative durations ("17d 10h") off the wall clock. Timers still run; a row that needs another instant sets its own after.
  await page.clock.setFixedTime(new Date(NG_NOW));
  // The live-events socket: accept and stay silent so the shell reads "live".
  await page.routeWebSocket(/\/api\/ws\/events/, (ws) => { if (opts.boardState === "offline") ws.close(); });
  // An action's events and updated_at at the frozen page clock's instant, not the machine's.
  const serve = mockServer(S, { now: () => NG_NOW, ...opts });
  await page.route(/\/api\//, async (route) => {
    const req = route.request();
    const reply = await serve({ method: req.method(), url: req.url(), json: () => req.postDataJSON() });
    if (reply === ABORT) return route.abort("connectionrefused");
    if (reply === HANG) return;
    return route.fulfill(reply);
  });
}
