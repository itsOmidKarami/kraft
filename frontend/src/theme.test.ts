import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyTheme, PALETTES } from "./theme";

function mockMatchMedia(initialMatches: boolean) {
  const listeners: ((e: MediaQueryListEvent) => void)[] = [];
  const mql: Partial<MediaQueryList> = {
    matches: initialMatches,
    media: "(prefers-color-scheme: dark)",
    addEventListener: vi.fn((_type, cb) => {
      listeners.push(cb as (e: MediaQueryListEvent) => void);
    }),
    removeEventListener: vi.fn((_type, cb) => {
      const i = listeners.indexOf(cb as (e: MediaQueryListEvent) => void);
      if (i >= 0) listeners.splice(i, 1);
    }),
  };
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue(mql));
  return {
    fire: (matches: boolean) => {
      for (const l of listeners) l({ matches } as MediaQueryListEvent);
    },
    listenerCount: () => listeners.length,
  };
}

beforeEach(() => {
  document.documentElement.removeAttribute("data-palette");
  document.documentElement.removeAttribute("data-mode");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PALETTES", () => {
  it("lists exactly the 5 shipped palettes", () => {
    expect(PALETTES.map((p) => p.id).sort()).toEqual(
      ["amber", "forest", "nocturne", "rose", "slate"].sort(),
    );
  });
});

describe("applyTheme", () => {
  it("sets data-palette and data-mode directly for light/dark", () => {
    applyTheme("forest", "dark");
    expect(document.documentElement.dataset.palette).toBe("forest");
    expect(document.documentElement.dataset.mode).toBe("dark");

    applyTheme("rose", "light");
    expect(document.documentElement.dataset.palette).toBe("rose");
    expect(document.documentElement.dataset.mode).toBe("light");
  });

  it("resolves system mode from prefers-color-scheme", () => {
    mockMatchMedia(true);
    applyTheme("nocturne", "system");
    expect(document.documentElement.dataset.mode).toBe("dark");
  });

  it("keeps data-mode in sync while system stays selected", () => {
    const media = mockMatchMedia(false);
    applyTheme("nocturne", "system");
    expect(document.documentElement.dataset.mode).toBe("light");

    media.fire(true);
    expect(document.documentElement.dataset.mode).toBe("dark");
  });

  it("tears down the system listener when switching away from system", () => {
    const media = mockMatchMedia(false);
    applyTheme("nocturne", "system");
    expect(media.listenerCount()).toBe(1);

    applyTheme("nocturne", "dark");
    expect(media.listenerCount()).toBe(0);

    // a change firing after teardown must not resurrect system behaviour
    media.fire(true);
    expect(document.documentElement.dataset.mode).toBe("dark");
  });
});
