/** Pure camera maths for the chain and node canvases, from Kraft Prototype V2's
 *  `fit`, `goCurrent`, `zoomAt`, `g2Fit` and `g2ZoomAt`. */
export type Cam = { tx: number; ty: number; s: number };
export type Size = { w: number; h: number };
export type CanvasKind = "chain" | "node";

/** Zoom limits: the chain canvas 0.3…2, the node canvas 0.3…2.5. */
export const ZOOM = { chain: { min: 0.3, max: 2 }, node: { min: 0.3, max: 2.5 } } as const;
export const STEP_IN = 1.25;
export const STEP_OUT = 0.8;
/** A background drag pans only after it has moved this far (px). */
export const DRAG_THRESHOLD = 4;
/** ⌘/ctrl-wheel zoom factor per wheel delta. */
export const wheelFactor = (deltaY: number) => Math.exp(-deltaY * 0.0025);

/** Zoom by `f` about the view point (px, py), which stays where it is. */
export function zoomAt(cam: Cam, px: number, py: number, f: number, canvas: CanvasKind): Cam {
  const { min, max } = ZOOM[canvas];
  const s = Math.min(max, Math.max(min, cam.s * f));
  const k = s / cam.s;
  return { s, tx: px - (px - cam.tx) * k, ty: py - (py - cam.ty) * k };
}

/** The whole world in view, never above 100% or below 30%, centred; the chain
 *  sits 10px above centre, and the node canvas keeps 20px off the left edge. */
export function fitCam(world: { W: number; H: number }, view: Size, canvas: CanvasKind): Cam {
  const s = Math.max(0.3, Math.min(1, (view.w - 40) / world.W, (view.h - 40) / world.H));
  const tx = (view.w - world.W * s) / 2;
  const ty = (view.h - world.H * s) / 2;
  return canvas === "chain" ? { s, tx, ty: ty - 10 } : { s, tx: Math.max(20, tx), ty };
}

/** 100%, the current node centred across and 30% down the view. */
export const currentCam = (cx: number, cy: number, view: Size): Cam => ({ s: 1, tx: view.w / 2 - cx, ty: Math.round(view.h * 0.3) - cy });

const LIVE = new Set(["running", "waiting", "needs_you", "escalated", "paused", "failed"]);
/** Decisions §7 "Opening view": an item with a node in progress opens on it at
 *  100%; one not started or finished opens fitted (R37). */
export const openingView = (status: string, hasCurrent: boolean): "current" | "fit" => (hasCurrent && LIVE.has(status) ? "current" : "fit");
