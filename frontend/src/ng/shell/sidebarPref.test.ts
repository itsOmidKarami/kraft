import { afterEach, describe, expect, it, vi } from "vitest";
import { applyInitialSidebar, currentSidebar, holdPane, readSidebar, SIDEBAR_KEY, writeSidebar } from "./sidebarPref";

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

describe("sidebar while a pane is open", () => {
  const at = (width: number) => vi.stubGlobal("innerWidth", width);
  const html = () => document.documentElement.dataset.sidebar;

  it.each([
    ["steps back to the rail at 1024 with nothing stored", 1024, null, "rail"],
    ["steps back at the 1279 edge", 1279, null, "rail"],
    ["stays pinned at 1280", 1280, null, "pinned"],
    ["stays pinned when the reader pinned it", 1024, "pinned", "pinned"],
    ["stays a rail when the reader chose that", 1024, "rail", "rail"],
  ] as const)("%s", (_n, width, stored, want) => {
    at(width);
    if (stored) localStorage.setItem(SIDEBAR_KEY, stored);
    const release = holdPane();
    expect(html()).toBe(want);
    release();
    vi.unstubAllGlobals();
  });

  it("returns when the pane closes and follows a resize while it is open", () => {
    at(1500);
    const release = holdPane();
    expect(html()).toBe("pinned");
    at(1000);
    window.dispatchEvent(new Event("resize"));
    expect(html()).toBe("rail");
    release();
    expect(html()).toBe("pinned");
    vi.unstubAllGlobals();
  });

  it("holds until the last of two panes closes", () => {
    at(1024);
    const a = holdPane();
    const b = holdPane();
    a();
    expect(html()).toBe("rail");
    b();
    expect(html()).toBe("pinned");
    vi.unstubAllGlobals();
  });
});
