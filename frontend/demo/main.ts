import { buildScenario } from "../e2e/contract/mocks/fixtures";
import { mockServer } from "../e2e/contract/mocks/mockApi";
import { NG_NOW } from "../e2e/contract/mocks/ngItems";
import { withoutBase } from "../src/ng/base";
import { showBadge } from "./badge";
import { patchFetch, patchWebSocket, restoreAddress, serveApiLinks, shiftClock, watch } from "./shim";
import { lookIn, Tries } from "./tries";

/**
 * The dashboard demo: the shipped SPA on the UI contract's scenario, answered in the
 * page, so it runs from static files with no Kraft server or agents. `just demo` runs
 * it; `npm run build:demo` builds it, and each docs version serves its own at <base>demo/.
 */
// The release this demo was built from, up to date, instead of the scenario's old version and its update notice.
const serve = mockServer(buildScenario("default"), { ngBoard: true, areas: "default", harnesses: "floor", version: __KRAFT_DEMO_VERSION__ });

// The things-to-try list ticks from what the page asks the mock, on the page the visitor is on.
const tries = new Tries(() => ({ pathname: withoutBase(location.pathname) ?? location.pathname, search: location.search }));

shiftClock(Date.parse(NG_NOW));
patchFetch(watch(serve, tries.see));
patchWebSocket();
serveApiLinks();
restoreAddress(import.meta.env.BASE_URL);
showBadge(new URL("../get-started/install", new URL(import.meta.env.BASE_URL, location.origin)).href, tries, lookIn(serve));
void import("../src/main");
