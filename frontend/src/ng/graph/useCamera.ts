import { useCallback, useEffect, useRef, useState, type PointerEvent as RPointerEvent, type MouseEvent as RMouseEvent } from "react";
import { currentCam, DRAG_THRESHOLD, fitCam, revealCam, STEP_IN, STEP_OUT, wheelFactor, zoomAt, type Cam, type CanvasKind, type FitRule, type Size } from "./camera";

export type CameraMode = "fit" | "current" | "free";
type Opts = {
  canvas: CanvasKind;
  world: { W: number; H: number };
  /** The current node's centre in world px, when there is one. */
  current?: { cx: number; cy: number };
  opening: "fit" | "current";
  /** Px on the right the docked pane takes from the view (0 when it overlays). */
  reserve?: number;
  /** Px on the right an overlaid pane covers (under 1024, R7): only the
   *  current-node framing keeps clear of it, so the node stays visible beside
   *  the pane; fit and panning keep the full width (Kraft-gvfm2). */
  cover?: number;
  /** An editor's fit: a floor and a left margin instead of centring (the Templates prototype). */
  fit?: FitRule;
};

/** Pan and zoom for one canvas viewport: pinch or ⌘-scroll zooms at the cursor,
 *  scroll or a background drag pans, fit and current re-frame. */
export function useCamera({ canvas, world, current, opening, reserve = 0, cover = 0, fit }: Opts) {
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [size, setSize] = useState<Size | null>(null);
  const [cam, setCam] = useState<Cam>({ tx: 0, ty: 0, s: 1 });
  const [mode, setMode] = useState<CameraMode>(opening === "current" && current ? "current" : "fit");
  const view: Size | null = size && { w: Math.max(0, size.w - reserve), h: size.h };
  const dragged = useRef(false);
  const camRef = useRef(cam);
  camRef.current = cam;

  useEffect(() => {
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);

  // Fit and current hold while the view or world changes; a manual move frees them.
  const cx = current?.cx, cy = current?.cy;
  useEffect(() => {
    if (!view || mode === "free") return;
    setCam(mode === "current" && cx != null && cy != null ? currentCam(cx, cy, { w: Math.max(0, view.w - cover), h: view.h }) : fitCam(world, view, canvas, fit));
  }, [mode, view?.w, view?.h, world.W, world.H, cx, cy, canvas, cover]); // eslint-disable-line react-hooks/exhaustive-deps

  // Non-passive, on the viewport only: preventDefault stops the page zooming or scrolling.
  useEffect(() => {
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) {
        const r = el.getBoundingClientRect();
        setCam((c) => zoomAt(c, e.clientX - r.left, e.clientY - r.top, wheelFactor(e.deltaY), canvas));
      } else setCam((c) => ({ ...c, tx: c.tx - e.deltaX, ty: c.ty - e.deltaY }));
      setMode("free");
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [el, canvas]);

  const onPointerDown = useCallback((e: RPointerEvent) => {
    if (e.button !== 0) return;
    const x0 = e.clientX, y0 = e.clientY;
    const start = camRef.current;
    dragged.current = false;
    const move = (ev: PointerEvent) => {
      const dx = ev.clientX - x0, dy = ev.clientY - y0;
      if (!dragged.current && Math.hypot(dx, dy) <= DRAG_THRESHOLD) return;
      dragged.current = true;
      setCam({ ...start, tx: start.tx + dx, ty: start.ty + dy });
      setMode("free");
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, []);
  /** A drag is never a click: swallow the click that ends one. */
  const onClickCapture = useCallback((e: RMouseEvent) => {
    if (dragged.current) { e.stopPropagation(); dragged.current = false; }
  }, []);

  const zoomCentre = (f: number) => {
    if (!view) return;
    setCam((c) => zoomAt(c, view.w / 2, view.h / 2, f, canvas));
    setMode("free");
  };
  return {
    cam, mode,
    bind: { ref: setEl, onPointerDown, onClickCapture },
    zoomIn: () => zoomCentre(STEP_IN),
    zoomOut: () => zoomCentre(STEP_OUT),
    reset: () => zoomCentre(1 / cam.s),
    fit: () => setMode("fit"),
    toCurrent: current ? () => setMode("current") : undefined,
    /** Pan just enough to show a world box, e.g. the node keyboard focus moved to. */
    reveal: (box: { x0: number; x1: number; y0: number; y1: number }) => {
      if (!view) return;
      const next = revealCam(camRef.current, box, view);
      if (next !== camRef.current) { setCam(next); setMode("free"); }
    },
  };
}
