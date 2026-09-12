import "@testing-library/jest-dom/vitest";

// jsdom has no matchMedia. Default every test to desktop width; tests that
// need phone width stub this themselves (see setPhoneWidth() in the settings
// test files) via vi.stubGlobal, which overrides this.
if (typeof window.matchMedia !== "function") {
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

// jsdom has no IntersectionObserver either (RightPane/Diff.tsx's pane-scroll
// → tree-highlight sync, G4-05). A no-op stub is enough: tests that care
// about the wiring call the callback directly rather than scrolling jsdom.
if (typeof window.IntersectionObserver !== "function") {
  class NoopIntersectionObserver implements IntersectionObserver {
    readonly root = null;
    readonly rootMargin = "";
    readonly thresholds: ReadonlyArray<number> = [];
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords(): IntersectionObserverEntry[] {
      return [];
    }
  }
  window.IntersectionObserver = NoopIntersectionObserver;
}
