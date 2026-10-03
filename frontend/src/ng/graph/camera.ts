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

/** How an editor fits (the Templates prototype's `fit1`): never under `floor`,
 *  and a world wider than the view starts `left` px from the left edge. */
export type FitRule = { floor: number; left: number };
/** The editors' fit (Templates prototype `fit1`): no smaller than 80%, a chain wider than the view starting 12px in. */
export const EDITOR_FIT: FitRule = { floor: 0.8, left: 12 };

/** The whole world in view, never above 100% or below 30%, centred; the chain
 *  sits 10px above centre, and the node canvas keeps 20px off the left edge. */
export function fitCam(world: { W: number; H: number }, view: Size, canvas: CanvasKind, rule?: FitRule): Cam {
  const s = Math.max(rule?.floor ?? 0.3, Math.min(1, (view.w - 40) / world.W, (view.h - 40) / world.H));
  const mid = (view.w - world.W * s) / 2;
  const tx = rule ? Math.max(rule.left, mid) : mid;
  const ty = (view.h - world.H * s) / 2;
  return canvas === "chain" ? { s, tx, ty: ty - 10 } : { s, tx: Math.max(20, tx), ty };
}

/** 100%, the current node centred across and 30% down the view. */
export const currentCam = (cx: number, cy: number, view: Size): Cam => ({ s: 1, tx: view.w / 2 - cx, ty: Math.round(view.h * 0.3) - cy });

const LIVE = new Set(["running", "waiting", "needs_you", "escalated", "paused"]);
/** Decisions §7 "Opening view": an item with a node in progress opens on it at
 *  100%; one not started, finished or failed opens fitted (R37, WI-4: a failure
 *  is read against the whole chain). */
export const openingView = (status: string, hasCurrent: boolean): "current" | "fit" => (hasCurrent && LIVE.has(status) ? "current" : "fit");

/** The least pan that brings a world box into view with `margin` px to spare
 *  (a node focused by keyboard must be visible). Unchanged when it already is. */
export function revealCam(cam: Cam, box: { x0: number; x1: number; y0: number; y1: number }, view: Size, margin = 24): Cam {
  const fit = (lo: number, hi: number, size: number) => (lo < margin ? margin - lo : hi > size - margin ? size - margin - hi : 0);
  const dx = fit(cam.tx + box.x0 * cam.s, cam.tx + box.x1 * cam.s, view.w);
  const dy = fit(cam.ty + box.y0 * cam.s, cam.ty + box.y1 * cam.s, view.h);
  return dx || dy ? { ...cam, tx: cam.tx + dx, ty: cam.ty + dy } : cam;
}
