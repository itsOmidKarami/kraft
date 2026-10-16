import { buildScenario } from "../e2e/contract/mocks/fixtures";
import { mockServer, type MockServer } from "../e2e/contract/mocks/mockApi";
import { NG_NOW } from "../e2e/contract/mocks/ngItems";
import { showBadge } from "./badge";
import { patchFetch, patchWebSocket, restoreAddress, serveApiLinks, shiftClock } from "./shim";

/**
 * The dashboard demo: the shipped SPA on the UI contract's scenario, answered in the
 * page, so it runs from static files with no Kraft server or agents. `just demo` runs
 * it; `npm run build:demo` builds it, and the docs site serves it at /demo/.
 */
const S = buildScenario("default");
// The release this demo was built from, up to date, instead of the fixture's old version and its update notice.
Object.assign(S.settings.health, { version: __KRAFT_DEMO_VERSION__, installed: __KRAFT_DEMO_VERSION__ });
const contract = mockServer(S, { ngBoard: true, areas: "default", harnesses: "floor" });
const serve: MockServer = async (req) => {
  const p = new URL(req.url).pathname;
  if (p === "/api/update" || p === "/api/update/check")
    return { status: 200, contentType: "application/json", body: JSON.stringify({ installed: __KRAFT_DEMO_VERSION__, latest: `v${__KRAFT_DEMO_VERSION__}`, channel: "stable", python: "3.14", behind: false, checked_at: new Date().toISOString() }) };
  return contract(req);
};

shiftClock(Date.parse(NG_NOW));
patchFetch(serve);
patchWebSocket();
serveApiLinks();
restoreAddress(import.meta.env.BASE_URL);
showBadge(new URL("../get-started/install", new URL(import.meta.env.BASE_URL, location.origin)).href);
void import("../src/main");
