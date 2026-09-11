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
