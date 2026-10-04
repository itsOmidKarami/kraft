import { afterEach } from "vitest";
import { iconOnlyProblem } from "./ng/ui/iconOnly";
import "@testing-library/jest-dom/vitest";

// jsdom has no matchMedia. Default every test to desktop width; tests that
// need phone width stub this themselves via vi.stubGlobal, which overrides
// this (see width() in ng/phone/App.phone.test.tsx).
if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as MediaQueryList;
}

// TT-1: a button or link that shows no text (an icon, or a glyph like × or ↑) must have an
// accessible name and a tooltip (ui/Tooltip marks its button `data-tip`). Every button
// any test renders is checked when the test ends, so a new icon-only button anywhere in
// ng/ fails the first test that draws it. The DOM is gone by then (RTL's cleanup runs
// first), but the elements are kept from when they were added.
if (typeof document !== "undefined") {
  const seen = new Set<HTMLElement>();
  // A sidebar row is a link whose label shows when the rail opens, not an icon-only control.
  const CONTROL = "button, a[href]:not(.ng-side-row)";
  const collect = (n: Node) => {
    if (!(n instanceof HTMLElement)) return;
    // Only controls React drew: a test's own anchor (`document.createElement("button")`) is not a control.
    const drawn = (b: HTMLElement) => Object.keys(b).some((k) => k.startsWith("__reactFiber"));
    if (n.matches(CONTROL) && drawn(n)) seen.add(n);
    n.querySelectorAll<HTMLElement>(CONTROL).forEach((b) => drawn(b) && seen.add(b));
  };
  new MutationObserver((rs) => rs.forEach((r) => r.addedNodes.forEach(collect))).observe(document, { childList: true, subtree: true });

  afterEach(() => {
    const found = [...seen].flatMap((b) => { const p = iconOnlyProblem(b); return p ? [`${p}: ${b.outerHTML.slice(0, 160)}`] : []; });
    seen.clear();
    if (found.length) throw new Error(`Icon-only buttons need an aria-label and a Tooltip (ui/IconButton does both):\n${[...new Set(found)].join("\n")}`);
  });
}
