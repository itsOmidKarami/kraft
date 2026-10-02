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
