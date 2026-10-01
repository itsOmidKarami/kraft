import { afterEach, describe, expect, it, vi } from "vitest";
import { applyInitialSidebar, defaultSidebar, readSidebar, SIDEBAR_KEY, writeSidebar } from "./sidebarPref";

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.sidebar;
  vi.restoreAllMocks();
});

describe("sidebar preference", () => {
  it("is pinned from 1280 up and a rail below", () => {
    expect([defaultSidebar(1279), defaultSidebar(1280), defaultSidebar(1920)]).toEqual(["rail", "pinned", "pinned"]);
  });

  it("lets a stored value win at any width", () => {
    localStorage.setItem(SIDEBAR_KEY, "pinned");
    vi.stubGlobal("innerWidth", 1024);
    applyInitialSidebar();
    expect(document.documentElement.dataset.sidebar).toBe("pinned");
    vi.unstubAllGlobals();
  });

  it("ignores a stored value that is neither choice", () => {
    localStorage.setItem(SIDEBAR_KEY, "wide");
    expect(readSidebar()).toBeNull();
  });

  it("writes storage and the attribute together", () => {
    writeSidebar("rail");
    expect(localStorage.getItem(SIDEBAR_KEY)).toBe("rail");
    expect(document.documentElement.dataset.sidebar).toBe("rail");
  });

  it("falls back to the width default, without throwing, when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });
    vi.stubGlobal("innerWidth", 1500);
    expect(() => applyInitialSidebar()).not.toThrow();
    expect(document.documentElement.dataset.sidebar).toBe("pinned");
    expect(() => writeSidebar("rail")).not.toThrow();
    expect(document.documentElement.dataset.sidebar).toBe("rail");
    vi.unstubAllGlobals();
  });

  it("sets the attribute before any promise resolves", async () => {
    let seen: string | undefined;
    void Promise.resolve().then(() => { seen = document.documentElement.dataset.sidebar; });
    applyInitialSidebar();
    await Promise.resolve();
    expect(seen).toBeDefined();
  });
});
