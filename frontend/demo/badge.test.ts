import { afterEach, describe, expect, it, vi } from "vitest";
import { PHONE_QUERY } from "../src/ng/phone/usePhone";
import { showBadge } from "./badge";
import { RING_MS, WAIT_MS } from "./ring";
import { Tries, type Look } from "./tries";

/** A board of one item, `w1`: its row and its detail are both `item`. */
const lookAt = (item: Record<string, unknown>): Look => ({ list: async () => [{ id: "w1", ...item }], item: async () => ({ id: "w1", ...item }) });
const AT_GATE = lookAt({ status: "needs_human", display_status: "needs_you", pending_gate: "spec_approval" });

const post = (path: string, body: unknown = null) => ({ method: "POST", url: `${location.origin}/api${path}`, json: () => body });
/** A board whose one item, at a gate, can be swapped for another: `board.at = "w2"`. */
const movable = () => {
  const board = { at: "w1", reads: 0 };
  const look: Look = { list: async () => (board.reads++, [{ id: board.at, pending_gate: "spec_approval" }]), item: async (id) => ({ id, pending_gate: "spec_approval" }) };
  return { board, look };
};
const boxed = (el: HTMLElement, top = 50) =>
  (el.getBoundingClientRect = () => ({ x: 40, y: top, left: 40, top, width: 120, height: 30, right: 160, bottom: top + 30, toJSON: () => ({}) }));

const width = (phone: boolean) =>
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: phone && query === PHONE_QUERY, media: query, addEventListener: () => {}, removeEventListener: () => {} }));

const show = (look: Look = AT_GATE) => {
  const tries = new Tries(() => ({ pathname: location.pathname, search: location.search }));
  const badge = showBadge("https://example.com/install", tries, look)!;
  const q = <T extends HTMLElement>(sel: string) => badge.querySelector<T>(sel)!;
  return { tries, badge, list: q(".kd-tries"), toggle: q<HTMLButtonElement>(".kd-toggle"), go: (id: string) => q<HTMLAnchorElement>(`[data-try="${id}"] .kd-try-go`), q };
};

afterEach(() => {
  vi.useRealTimers();
  document.body.replaceChildren();
  document.head.querySelectorAll("style").forEach((e) => e.remove());
  try { localStorage.clear(); sessionStorage.clear(); } catch { /* a stubbed one */ }
  window.history.replaceState(null, "", "/");
});

describe("the list's first-visit memory", () => {
  const throwing = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); }, clear: () => {} };
  it.each<[string, string | null | "throws", boolean, boolean, string | null]>([
    ["a first visit on a desktop opens it, and remembers it shut for next time", null, false, true, "closed"],
    ["a first visit on a phone keeps it shut", null, true, false, "closed"],
    ["a visitor who left it open finds it open, even on a phone", "open", true, true, "open"],
    ["a visitor who shut it finds it shut", "closed", false, false, "closed"],
    ["with storage refused, a desktop opens it every load", "throws", false, true, null],
    ["with storage refused, a phone keeps it shut", "throws", true, false, null],
  ])("%s", (_, stored, phone, open, after) => {
    width(phone);
    if (stored === "throws") vi.stubGlobal("localStorage", throwing);
    else if (stored) localStorage.setItem("kraft.demo.tries", stored);
    const { list, toggle } = show();
    expect([list.hidden, toggle.getAttribute("aria-expanded")]).toEqual([!open, String(open)]);
    if (stored !== "throws") expect(localStorage.getItem("kraft.demo.tries")).toBe(after);
  });

  it("remembers what a toggle left it as", () => {
    width(false);
    const { toggle } = show();
    toggle.click();
    const shut = localStorage.getItem("kraft.demo.tries");
    toggle.click();
    expect([shut, localStorage.getItem("kraft.demo.tries")]).toEqual(["closed", "open"]);
  });

  it("stops listening after the ×: a later tick or request touches nothing", () => {
    width(false);
    const { board, look } = movable();
    const { q, tries, toggle } = show(look);
    const reads = board.reads;
    q<HTMLButtonElement>(".kd-x").click();
    tries.see(post("/work-items/w1/gates/spec_approval/approve"), { status: 200 });
    expect([toggle.getAttribute("aria-label"), board.reads]).toEqual(["Things to try, 0 of 5 done", reads]);
  });

  it("goes away with the badge's × for the rest of the visit", () => {
    width(false);
    const { q } = show();
    q<HTMLButtonElement>(".kd-x").click();
    expect([document.querySelector(".kd-badge"), sessionStorage.getItem("kraft.demo.badge.hidden")]).toEqual([null, "1"]);
    expect(showBadge("https://example.com/install", new Tries(() => ({ pathname: "/", search: "" })), AT_GATE)).toBeUndefined();
  });
});

describe("Escape", () => {
  it.each([
    ["inside the list shuts it, gives focus back to the toggle, and goes no further", "list", [true, 0, true]],
    ["on the badge's toggle, outside the list, leaves it open for the app", "toggle", [false, 1, true]],
    ["on the page leaves the list open, and the app hears it", "page", [false, 1, false]],
  ])("%s", (_, where, outcome) => {
    width(false);
    const { list, toggle, go } = show();
    const app = vi.fn();
    window.addEventListener("keydown", app);
    const from = where === "list" ? go("gate") : where === "toggle" ? toggle : document.body;
    from.focus();
    from.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    window.removeEventListener("keydown", app);
    expect([list.hidden, app.mock.calls.length, document.activeElement === toggle]).toEqual(outcome);
  });
});

describe("Show me", () => {
  const ready = async (go: HTMLAnchorElement) => vi.waitFor(() => expect(go.getAttribute("href")).toBe("/work-items/w1"));

  it.each([
    ["a plain click goes there by the router", {}, false, "/work-items/w1", 1],
    ["a click the leave guard already took is left to it", {}, true, "/", 0],
    ["a ctrl-click is left to the browser (a new tab)", { ctrlKey: true }, false, "/", 0],
    ["a middle-button click is left to the browser", { button: 1 }, false, "/", 0],
  ])("%s", async (_, keys, taken, path, pops) => {
    width(false);
    const { go } = show();
    await ready(go("gate"));
    const guard = (e: Event) => taken && e.preventDefault();
    const popped = vi.fn();
    let ours: boolean | null = null;
    // What the browser would do next: note whether the demo took the click, then keep jsdom from navigating.
    const after = (e: Event) => { ours ??= e.defaultPrevented; e.preventDefault(); };
    document.addEventListener("click", guard, true);
    document.addEventListener("click", after);
    window.addEventListener("popstate", popped);
    go("gate").dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, ...keys }));
    document.removeEventListener("click", guard, true);
    document.removeEventListener("click", after);
    window.removeEventListener("popstate", popped);
    expect([location.pathname, popped.mock.calls.length, ours]).toEqual([path, pops, taken || pops > 0]);
  });

  it("pushes the router's own kind of entry, one index on, so Back and the phone's trail stay right", async () => {
    width(false);
    window.history.replaceState({ usr: null, key: "start", idx: 3 }, "", "/");
    const { go } = show();
    await ready(go("gate"));
    go("gate").click();
    expect(window.history.state).toEqual({ usr: null, key: expect.stringMatching(/^[0-9a-z]+$/), idx: 4 });
  });

  it("on the page it already names, pushes nothing and only rings", async () => {
    width(false);
    window.history.replaceState({ idx: 2 }, "", "/work-items/w1");
    const { go } = show();
    await ready(go("gate"));
    const [length, popped] = [window.history.length, vi.fn()];
    window.addEventListener("popstate", popped);
    vi.useFakeTimers();
    go("gate").click();
    const target = document.body.appendChild(Object.assign(document.createElement("button"), { textContent: "Review" }));
    boxed(target);
    vi.advanceTimersByTime(150);
    window.removeEventListener("popstate", popped);
    expect([window.history.length - length, popped.mock.calls.length, window.history.state, !!document.querySelector(".kd-ring")]).toEqual([0, 0, { idx: 2 }, true]);
  });

  it.each([
    ["while the list is open, a request that may move things reads where each Show me goes again", "open", "/work-items/w2"],
    ["while it is shut, nothing is read until it opens", "closed", "/"],
  ])("%s", async (_, state, href) => {
    width(false);
    localStorage.setItem("kraft.demo.tries", state);
    const { board, look } = movable();
    const { go, tries } = show(look);
    if (state === "open") await ready(go("gate"));
    const reads = board.reads;
    board.at = "w2";
    tries.see(post("/work-items/w9/pause"), { status: 200 });
    await new Promise((r) => setTimeout(r, 20));
    expect([go("gate").getAttribute("href"), board.reads > reads]).toEqual([href, state === "open"]);
  });

  it.each([
    ["rings its target once the page draws it, then lets it go", true],
    ["draws no ring when the page never lays it out (the phone layout)", false],
  ])("%s", async (_, drawn) => {
    width(false);
    const { go } = show();
    await ready(go("gate"));
    vi.useFakeTimers();
    go("gate").click();
    // jsdom lays nothing out: a target left as it is has no box, as one display:none'd on a phone has none.
    const target = Object.assign(document.createElement("button"), { textContent: "Review" });
    if (drawn) target.getBoundingClientRect = () => ({ x: 40, y: 50, left: 40, top: 50, width: 120, height: 30, right: 160, bottom: 80, toJSON: () => ({}) });
    document.body.append(target);
    vi.advanceTimersByTime(150);
    const ring = document.querySelector<HTMLElement>(".kd-ring");
    const placed = ring && [ring.style.left, ring.style.top, ring.style.width];
    vi.advanceTimersByTime(Math.max(RING_MS, WAIT_MS));
    expect([placed, document.querySelector(".kd-ring")]).toEqual([drawn ? ["36px", "46px", "128px"] : null, null]);
  });

  it.each([
    ["on a phone, where the list covers the page, it shuts and the toggle has focus", true, [true, true, "open"]],
    ["on a desktop the list stays open, to go down it", false, [false, false, "open"]],
  ])("%s", async (_, phone, outcome) => {
    width(phone);
    localStorage.setItem("kraft.demo.tries", "open");
    const { go, list, toggle } = show();
    await ready(go("gate"));
    go("gate").focus();
    go("gate").click();
    expect([list.hidden, document.activeElement === toggle, localStorage.getItem("kraft.demo.tries")]).toEqual(outcome);
  });

  it("says one is on its way when no item waits at a gate", async () => {
    width(false);
    const { go, q } = show(lookAt({ status: "active", display_status: "running", pending_gate: null }));
    await vi.waitFor(() => expect(q('[data-try="gate"] .kd-try-none').hidden).toBe(false));
    expect([go("gate").hidden, q('[data-try="gate"] .kd-try-none').textContent]).toEqual([true, "No item waits at a gate right now; one is on its way."]);
  });
});

describe("the ticks", () => {
  it("tick the entry the shim saw done, and count it on the toggle", () => {
    width(false);
    const { tries, toggle, q } = show();
    tries.see(post("/work-items/w1/resume", { steer: "look at the cache" }), { status: 200 });
    expect([q('[data-try="steer"]').classList.contains("is-done"), q('[data-try="gate"]').classList.contains("is-done"), toggle.getAttribute("aria-label")])
      .toEqual([true, false, "Things to try, 1 of 5 done"]);
  });

  it("say each new tick once, politely, outside the list so a shut one is heard too", () => {
    width(false);
    localStorage.setItem("kraft.demo.tries", "closed");
    const { tries, q } = show();
    const status = q("[role=status]");
    tries.see(post("/work-items/w1/resume", { steer: "look at the cache" }), { status: 200 });
    const first = status.textContent;
    tries.see(post("/work-items/w1/pause"), { status: 200 });
    const unchanged = status.textContent;
    tries.see(post("/work-items/w1/gates/spec_approval/approve"), { status: 200 });
    expect([first, unchanged, status.textContent, q(".kd-tries").contains(status)])
      .toEqual(["Done: Steer an agent. 1 of 5.", "Done: Steer an agent. 1 of 5.", "Done: Approve a gate. 2 of 5.", false]);
  });
});
