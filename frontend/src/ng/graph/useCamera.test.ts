import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { currentCam, fitCam, openingView, revealCam, wheelFactor, zoomAt, ZOOM } from "./camera";
import { useCamera } from "./useCamera";

describe("camera maths", () => {
  it("fits a wide world by width, centred, the chain 10px above centre", () => {
    // (1040 − 40) / 2000 = 0.5 < (440 − 40) / 250.
    expect(fitCam({ W: 2000, H: 250 }, { w: 1040, h: 440 }, "chain")).toEqual({ s: 0.5, tx: 20, ty: 157.5 - 10 });
  });
  it("fits a tall world by height", () => {
    expect(fitCam({ W: 400, H: 1040 }, { w: 1000, h: 560 }, "node").s).toBe(0.5);
  });
  it("never fits above 100% or below 30%", () => {
    expect(fitCam({ W: 100, H: 100 }, { w: 1000, h: 1000 }, "chain").s).toBe(1);
    expect(fitCam({ W: 100000, H: 100 }, { w: 1000, h: 1000 }, "chain").s).toBe(0.3);
  });
  it("keeps the fitted node canvas 20px off the left edge", () => {
    expect(fitCam({ W: 10000, H: 100 }, { w: 1000, h: 500 }, "node").tx).toBe(20);
    // An editor's fit (Templates prototype fit1): an 80% floor, a long chain starting 12px in.
    expect(fitCam({ W: 2600, H: 250 }, { w: 690, h: 700 }, "chain", { floor: 0.8, left: 12 })).toEqual({ s: 0.8, tx: 12, ty: (700 - 200) / 2 - 10 });
    expect(fitCam({ W: 400, H: 250 }, { w: 1000, h: 700 }, "chain", { floor: 0.8, left: 12 }).tx).toBe(300);
  });
  it("centres the current node across and 30% down at 100%", () => {
    expect(currentCam(500, 96, { w: 800, h: 401 })).toEqual({ s: 1, tx: -100, ty: 120 - 96 });
  });
  it("zooms about a fixed point", () => {
    const c = zoomAt({ tx: 10, ty: 20, s: 1 }, 110, 70, 1.25, "chain");
    expect(c.s).toBe(1.25);
    // The world point under (110, 70) was (100, 50); it still is.
    expect([(110 - c.tx) / c.s, (70 - c.ty) / c.s]).toEqual([100, 50]);
  });
  it("clamps the chain canvas to 0.3…2 and the node canvas to 0.3…2.5", () => {
    expect(ZOOM).toEqual({ chain: { min: 0.3, max: 2 }, node: { min: 0.3, max: 2.5 } });
    expect(zoomAt({ tx: 0, ty: 0, s: 1.8 }, 0, 0, 2, "chain").s).toBe(2);
    expect(zoomAt({ tx: 0, ty: 0, s: 1.8 }, 0, 0, 2, "node").s).toBe(2.5);
    expect(zoomAt({ tx: 0, ty: 0, s: 0.4 }, 0, 0, 0.5, "node").s).toBe(0.3);
  });
  it("zooms the wheel by exp(−deltaY · 0.0025)", () => {
    expect(wheelFactor(-400)).toBeCloseTo(Math.E);
  });
  it("pans just enough to show a box, 24px in from the edge, and not at all when it shows", () => {
    const cam = { tx: 0, ty: 0, s: 0.5 }, view = { w: 400, h: 300 };
    expect(revealCam(cam, { x0: 100, x1: 200, y0: 100, y1: 200 }, view)).toBe(cam);
    // Right edge at 900 · 0.5 = 450 → 376.
    expect(revealCam(cam, { x0: 800, x1: 900, y0: 100, y1: 200 }, view)).toEqual({ tx: -74, ty: 0, s: 0.5 });
    // Left edge at −20 · 0.5 = −10 → 24; top at −40 · 0.5 = −20 → 24.
    expect(revealCam(cam, { x0: -20, x1: 0, y0: -40, y1: 0 }, view)).toEqual({ tx: 34, ty: 44, s: 0.5 });
    expect(revealCam(cam, { x0: 0, x1: 10, y0: 600, y1: 620 }, view).ty).toBe(300 - 24 - 310);
  });
  it("opens on the current node only while one is in progress", () => {
    for (const s of ["running", "waiting", "needs_you", "escalated", "paused", "failed"]) expect(openingView(s, true)).toBe("current");
    for (const s of ["queued", "done", "cancelled", "archived"]) expect(openingView(s, true)).toBe("fit");
    expect(openingView("running", false)).toBe("fit");
  });
});

function mount(opts: Parameters<typeof useCamera>[0]) {
  const el = document.createElement("div");
  Object.defineProperties(el, { clientWidth: { value: 1040 }, clientHeight: { value: 440 } });
  el.getBoundingClientRect = () => ({ left: 0, top: 0 }) as DOMRect;
  document.body.append(el);
  const h = renderHook(() => useCamera(opts));
  act(() => h.result.current.bind.ref(el));
  return { el, h };
}
const down = (h: ReturnType<typeof mount>["h"], x: number) => act(() => h.result.current.bind.onPointerDown({ button: 0, clientX: x, clientY: 0 } as never));
const move = (x: number) => act(() => void window.dispatchEvent(Object.assign(new Event("pointermove"), { clientX: x, clientY: 0 })));

describe("useCamera", () => {
  it("opens fitted, and a pan past 4px frees it and swallows the click", () => {
    const { h } = mount({ canvas: "chain", world: { W: 2000, H: 250 }, opening: "fit" });
    expect(h.result.current.cam.s).toBe(0.5);
    down(h, 0);
    move(4);
    expect(h.result.current.mode).toBe("fit");
    move(30);
    expect(h.result.current.mode).toBe("free");
    expect(h.result.current.cam.tx).toBe(20 + 30);
    const click = { stopPropagation: () => (click.stopped = true), stopped: false };
    h.result.current.bind.onClickCapture(click as never);
    expect(click.stopped).toBe(true);
  });
  it("keeps the current node clear of an overlaid pane, while fit keeps the full width (Kraft-gvfm2)", () => {

    const { h } = mount({ canvas: "chain", world: { W: 2000, H: 250 }, opening: "current", current: { cx: 500, cy: 96 }, cover: 380 });
    expect(h.result.current.cam).toEqual({ s: 1, tx: 330 - 500, ty: 132 - 96 });
    act(() => h.result.current.fit());
    const covered = h.result.current.cam;
    const { h: plain } = mount({ canvas: "chain", world: { W: 2000, H: 250 }, opening: "fit" });
    expect(covered).toEqual(plain.result.current.cam);
  });

  it("opens on the current node, and the reserve narrows the view", () => {
    const { h } = mount({ canvas: "chain", world: { W: 2000, H: 250 }, opening: "current", current: { cx: 500, cy: 96 }, reserve: 440 });
    expect(h.result.current.cam).toEqual({ s: 1, tx: 300 - 500, ty: 132 - 96 });
  });
  it("zooms at the cursor on ⌘-wheel and pans on a plain wheel", () => {
    const { el, h } = mount({ canvas: "chain", world: { W: 100, H: 100 }, opening: "fit" });
    act(() => void el.dispatchEvent(Object.assign(new Event("wheel", { cancelable: true }), { deltaX: 0, deltaY: 50, metaKey: false })));
    expect(h.result.current.cam.ty).toBe(170 - 10 - 50);
    act(() => void el.dispatchEvent(Object.assign(new Event("wheel", { cancelable: true }), { deltaX: 0, deltaY: -400, ctrlKey: true, clientX: 0, clientY: 0 })));
    expect(h.result.current.cam.s).toBe(2);
  });
});
