import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyTheme, cachedLook, DEFAULT_LOOK, type Look } from "./applyTheme";

const html = () => document.documentElement.dataset;
const look = (over: Partial<Look> = {}): Look => ({ ...DEFAULT_LOOK, ...over });

function mockScheme(dark: boolean) {
  const listeners = new Set<() => void>();
  const mql = {
    matches: dark,
    addEventListener: (_: string, f: () => void) => listeners.add(f),
    removeEventListener: (_: string, f: () => void) => listeners.delete(f),
  };
  vi.stubGlobal("matchMedia", () => mql);
  return {
    set: (d: boolean) => {
      mql.matches = d;
      listeners.forEach((f) => f());
    },
    listeners,
  };
}

beforeEach(() => {
  localStorage.clear();
  for (const k of ["surface", "accent", "amount", "mode", "density", "palette"]) delete html()[k];
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ng applyTheme", () => {
  it("sets the data attributes theme.css keys on, and leaves the shipped UI's alone", () => {
    localStorage.setItem("kraft.theme", "shipped");
    applyTheme(look({ surface: "moss", accent: "rose", colour_amount: "full", mode: "light", density: "comfortable" }));
    expect({ ...html() }).toEqual({ surface: "moss", accent: "rose", amount: "full", mode: "light", density: "comfortable" });
    expect(localStorage.getItem("kraft.theme")).toBe("shipped");
  });

  it("caches the look under kraft.theme.v2 and reads it back", () => {
    expect(cachedLook()).toBeNull();
    const l = look({ surface: "sand", mode: "system" });
    applyTheme(l);
    expect(JSON.parse(localStorage.getItem("kraft.theme.v2")!)).toEqual(l);
    expect(cachedLook()).toEqual(l);
  });

  it("follows prefers-color-scheme under system, and stops on a later mode", () => {
    const scheme = mockScheme(true);
    applyTheme(look({ mode: "system" }));
    expect(html().mode).toBe("dark");
    scheme.set(false);
    expect(html().mode).toBe("light");

    applyTheme(look({ mode: "dark" }));
    expect(scheme.listeners.size).toBe(0);
    scheme.set(false);
    expect(html().mode).toBe("dark");
  });

  it("paints the default when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(cachedLook()).toBeNull();
    applyTheme(cachedLook() ?? DEFAULT_LOOK);
    expect({ ...html() }).toEqual({ surface: "graphite", accent: "none", amount: "subtle", mode: "dark", density: "compact" });
  });
});
