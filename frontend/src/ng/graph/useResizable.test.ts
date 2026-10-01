import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clampPane, PANE, useResizable } from "./useResizable";

const media = (narrow: boolean) => {
  const listeners: (() => void)[] = [];
  const mq = { matches: narrow, addEventListener: (_: string, f: () => void) => listeners.push(f), removeEventListener: () => {} };
  vi.stubGlobal("matchMedia", () => mq);
  return { flip: (v: boolean) => { mq.matches = v; listeners.forEach((f) => f()); } };
};
const key = (k: string) => ({ key: k, preventDefault() {}, stopPropagation() {} }) as never;

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("pane width", () => {
  it("clamps between 300 and the canvas minus 460, with 380 by default", () => {
    expect(PANE).toMatchObject({ MIN: 300, RESERVE: 460, DEFAULT: 380 });
    expect(clampPane(200, 1400)).toBe(300);
    expect(clampPane(1000, 1400)).toBe(940);
    expect(clampPane(500, 1400)).toBe(500);
    // A canvas narrower than 760 still gets 300.
    expect(clampPane(380, 700)).toBe(300);
  });

  it("is remembered per page and re-clamped to the canvas", () => {
    media(false);
    localStorage.setItem("kraft.ng.pane.item", "900");
    const { result, rerender } = renderHook(({ c }) => useResizable("item", c), { initialProps: { c: 1600 } });
    expect(result.current.width).toBe(900);
    rerender({ c: 1200 });
    expect(result.current.width).toBe(740);
    expect(renderHook(() => useResizable("board", 1600)).result.current.width).toBe(380);
  });

  it("moves by 16px on ←/→, to the ends on Home/End, and saves each step", () => {
    media(false);
    const { result } = renderHook(() => useResizable("item", 1400));
    act(() => result.current.handle!.onKeyDown(key("ArrowLeft")));
    expect(result.current.width).toBe(396);
    expect(localStorage.getItem("kraft.ng.pane.item")).toBe("396");
    act(() => result.current.handle!.onKeyDown(key("ArrowRight")));
    act(() => result.current.handle!.onKeyDown(key("ArrowRight")));
    expect(result.current.width).toBe(364);
    act(() => result.current.handle!.onKeyDown(key("End")));
    expect(result.current.width).toBe(940);
    expect(result.current.handle).toMatchObject({ role: "separator", "aria-valuenow": 940, "aria-valuemin": 300, "aria-valuemax": 940 });
    act(() => result.current.handle!.onKeyDown(key("Home")));
    expect(localStorage.getItem("kraft.ng.pane.item")).toBe("300");
  });

  it("drags by the inner edge and saves on release", () => {
    media(false);
    const { result } = renderHook(() => useResizable("item", 1400));
    act(() => result.current.handle!.onPointerDown({ clientX: 1000, preventDefault() {}, stopPropagation() {} } as never));
    act(() => void window.dispatchEvent(Object.assign(new Event("pointermove"), { clientX: 900 })));
    expect(result.current.width).toBe(480);
    expect(localStorage.getItem("kraft.ng.pane.item")).toBeNull();
    act(() => void window.dispatchEvent(new Event("pointerup")));
    expect(localStorage.getItem("kraft.ng.pane.item")).toBe("480");
  });

  it("survives storage that throws", () => {
    media(false);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("private"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("private"); });
    const { result } = renderHook(() => useResizable("item", 1400));
    expect(result.current.width).toBe(380);
    act(() => result.current.handle!.onKeyDown(key("ArrowLeft")));
    expect(result.current.width).toBe(396);
    vi.restoreAllMocks();
  });

  it("overlays under 1024 at min(380, canvas − 40), with no handle, live (R7)", () => {
    const m = media(true);
    const { result } = renderHook(() => useResizable("item", 400));
    expect(result.current).toMatchObject({ overlay: true, width: 360, handle: undefined });
    act(() => m.flip(false));
    expect(result.current.overlay).toBe(false);
  });
});
