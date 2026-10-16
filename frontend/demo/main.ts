import { buildScenario } from "../e2e/contract/mocks/fixtures";
import { mockServer } from "../e2e/contract/mocks/mockApi";
import { NG_NOW } from "../e2e/contract/mocks/ngItems";
import { withoutBase } from "../src/ng/base";
import { showBadge } from "./badge";
import { broadcast, patchFetch, patchWebSocket, restoreAddress, serveApiLinks, shiftClock, watch, whileShown } from "./shim";
import { lookIn, Tries } from "./tries";

/**
 * The dashboard demo: the shipped SPA on the UI contract's scenario, answered in the
 * page, so it runs from static files with no Kraft server or agents. `just demo` runs
 * it; `npm run build:demo` builds it, and each docs version serves its own at <base>demo/.
 */
// The things-to-try list ticks from what the page asks the mock, on the page the visitor is on.
const tries = new Tries(() => ({ pathname: withoutBase(location.pathname) ?? location.pathname, search: location.search }));

// The release this demo was built from, up to date, instead of the scenario's old version and its update notice.
// Each event an action appends goes out on the events socket, so the board moves as the item does; `motion`
// walks the running items along their chains on their own, while the tab is shown. The list hears each event
// too: where its Show me's go may have moved with it.
const onEvent: typeof broadcast = (e) => {
  broadcast(e);
  tries.moved();
};
const serve = mockServer(buildScenario("default"), { ngBoard: true, areas: "default", harnesses: "floor", version: __KRAFT_DEMO_VERSION__, onEvent, motion: true });

shiftClock(Date.parse(NG_NOW));
patchFetch(watch(serve, tries.see));
patchWebSocket();
serveApiLinks();
whileShown(serve.motion!);
restoreAddress(import.meta.env.BASE_URL);
showBadge(new URL("../get-started/install", new URL(import.meta.env.BASE_URL, location.origin)).href, tries, lookIn(serve));
void import("../src/main");
