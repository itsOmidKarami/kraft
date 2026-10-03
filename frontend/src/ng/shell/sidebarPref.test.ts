import { afterEach, describe, expect, it, vi } from "vitest";
import { applyInitialSidebar, currentSidebar, readSidebar, SIDEBAR_KEY, writeSidebar } from "./sidebarPref";

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.sidebar;
  vi.restoreAllMocks();
});

describe("sidebar preference", () => {
  it.each([390, 1024, 1279, 1920])("is pinned at %ipx with nothing stored", (width) => {
    vi.stubGlobal("innerWidth", width);
    expect(currentSidebar()).toBe("pinned");
    vi.unstubAllGlobals();
  });

  it("lets a stored value win at any width", () => {
    localStorage.setItem(SIDEBAR_KEY, "rail");
    vi.stubGlobal("innerWidth", 1024);
    applyInitialSidebar();
    expect(document.documentElement.dataset.sidebar).toBe("rail");
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

  it("falls back to pinned, without throwing, when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });
    vi.stubGlobal("innerWidth", 1024);
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
