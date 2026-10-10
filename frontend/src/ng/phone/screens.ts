/** Every screen the phone has, and the taps that reach it from the board (W17 brief P.1). Pure data, no imports: the vitest walk (`reach.test.tsx`) and the UI contract (`e2e/contract/phone.spec.ts`) both read it, so "reachable by tapping from the board" is a recorded fact. */

/** Which seeded item a card tap opens: the harness maps each to an item of its own data. */
export type Card = "running" | "capped" | "gate" | "escalated";

/** One tap: a control by role and name (a regular expression source, case-insensitive), or the first element a selector matches; `card` says which board card a selector tap means. */
export type Tap = { role: "link" | "button" | "tab"; name: string } | { css: string; card?: Card };

export interface PhoneScreen {
  id: string;
  /** The route pattern in `PhoneApp`. */
  route: string;
  /** From the board, in order. */
  taps: Tap[];
  /** The `<h1>` the screen shows; absent when it is data (an item's title). */
  heading?: string;
  /** The data the taps need (a card, a row): the vitest walk covers a screen without it, the UI contract's phone rows the rest. */
  data?: boolean;
  /** Data the default seed does not have, which the UI contract adds by this name: a test task with scopes. */
  seed?: "scopes";
  /** A query parameter the address carries there, when the route alone is another screen's too. */
  query?: string;
}

const tab = (name: string): Tap => ({ role: "link", name: `^${name}$` });
const more = (to: string): Tap[] => [tab("More"), { role: "link", name: `^${to}` }];
const card = (c: Card): Tap => ({ css: ".ph-card-main", card: c });
const row: Tap = { css: "a.ph-row" };
const node: Tap = { css: ".ph-node-row" };

export const SCREENS: PhoneScreen[] = [
  { id: "board", route: "/", taps: [], heading: "Board" },
  { id: "item-running", route: "/work-items/:id", taps: [card("running")], data: true },
  { id: "item-capped", route: "/work-items/:id", taps: [card("capped")], data: true },
  { id: "item-gate", route: "/work-items/:id", taps: [card("gate")], data: true },
  { id: "item-escalated", route: "/work-items/:id", taps: [card("escalated")], data: true },
  { id: "node", route: "/work-items/:id/nodes/:node", taps: [card("running"), node], data: true },
  { id: "task", route: "/work-items/:id/nodes/:node", taps: [card("running"), node, { css: ".ph-task-row" }], data: true },
  { id: "scope", route: "/work-items/:id/nodes/:node", taps: [card("running"), { role: "button", name: "^verification" }, { role: "button", name: "^unit_tests" }, { role: "button", name: "^just test-unit" }], data: true, seed: "scopes", query: "scope" },
  { id: "gate-node", route: "/work-items/:id/nodes/:node", taps: [card("gate"), node], data: true },
  { id: "review", route: "/work-items/:id/review", taps: [card("gate"), { role: "button", name: "^Review and decide$" }], data: true },
  { id: "new", route: "/work-items/new", taps: [{ role: "link", name: "^New work item$" }], heading: "New work item" },
  { id: "search", route: "/search", taps: [tab("Search")], heading: "Search" },
  { id: "analytics", route: "/analytics", taps: [tab("Analytics")], heading: "Analytics" },
  { id: "more", route: "/more", taps: [tab("More")], heading: "More" },
  { id: "chains", route: "/templates/chains", taps: more("Chains"), heading: "Chains" },
  { id: "chain", route: "/templates/chains/:chain", taps: [...more("Chains"), row], data: true },
  { id: "chain-node", route: "/templates/chains/:chain/nodes/:node", taps: [...more("Chains"), row, { css: "a.ph-row[href*='/nodes/']" }], data: true },
  { id: "library", route: "/templates/library", taps: more("Library"), heading: "Library" },
  { id: "library-component", route: "/templates/library/:ref", taps: [...more("Library"), row], data: true },
  { id: "harnesses", route: "/settings/harnesses", taps: more("Harnesses"), heading: "Harnesses" },
  { id: "harness", route: "/settings/harnesses/:id", taps: [...more("Harnesses"), { css: "a.ph-row[href*='/settings/harnesses/']:not([href*='/profiles/'])" }], data: true },
  { id: "profile", route: "/settings/harnesses/profiles/:name", taps: [...more("Harnesses"), { css: "a.ph-row[href*='/profiles/']" }], data: true },
  { id: "repos", route: "/settings/repos", taps: more("Repos"), heading: "Repos" },
  { id: "repo", route: "/settings/repos/:repo", taps: [...more("Repos"), row], data: true },
  { id: "policy", route: "/settings/policy/:section", taps: more("Policy"), heading: "Policy" },
  { id: "intake", route: "/settings/auto-intake", taps: more("Auto-intake"), heading: "Auto-intake" },
  { id: "schedule", route: "/settings/auto-intake/schedules/:index", taps: [...more("Auto-intake"), { css: "a.ph-row[href*='/schedules/']" }], data: true },
  { id: "notifications", route: "/settings/notifications", taps: more("Notifications"), heading: "Notifications" },
  { id: "channel", route: "/settings/notifications/:channel", taps: [...more("Notifications"), { role: "link", name: "^Webhook" }], heading: "Webhook" },
  { id: "access", route: "/settings/access", taps: more("Access"), heading: "Access" },
  { id: "appearance", route: "/settings/appearance", taps: more("Appearance"), heading: "Appearance" },
  { id: "about", route: "/settings/about", taps: more("About"), heading: "About" },
  { id: "archived", route: "/archived", taps: more("Archived"), heading: "Archived" },
];
