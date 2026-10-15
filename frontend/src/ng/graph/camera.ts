/** Pure camera maths for the chain and node canvases, from Kraft Prototype V2's
 *  `fit`, `goCurrent`, `zoomAt`, `g2Fit` and `g2ZoomAt`. */
import { L, START } from "./layout";
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
 *  and a world wider than the view starts `left` px from the left edge, or centred on `focus` when it has one. */
/** `initial`: the floor is for the opening view only; the Fit button then shows the whole chain. */
export type FitRule = { floor: number; left: number; focus?: number; initial?: boolean; start?: number };
/** The editors' fit (Templates prototype `fit1`): no smaller than 80%, a chain wider than the view starting 12px in. */
export const EDITOR_FIT: FitRule = { floor: 0.8, left: 12 };
/** An item's fit: node labels (11.5px) and captions (11px) stay at 10px or more, and a chain too wide for it opens on the node that matters (`focus`, a
 *  world x) instead of at a 30% fit nobody can read; the canvas counts the nodes that are left off-screen. `start`, the world x of the chain's first mark's left edge,
 *  is kept `left` px in while the focus still fits beside it. */
export const ITEM_FIT: FitRule = { floor: 0.95, left: 12, initial: true, start: START.cx - START.h };
/** Half the widest column: the room a focused node needs to the right of its centre. */
const FOCUS_HALF = L.COLE / 2;

/** The whole world in view, never above 100% or below 30%, centred; the chain's
 *  row of nodes and labels sits at the middle of the view (as far as the world
 *  allows), and the node canvas keeps 20px off the left edge. */
export function fitCam(world: { W: number; H: number }, view: Size, canvas: CanvasKind, rule?: FitRule): Cam {
  const s = Math.max(rule?.floor ?? 0.3, Math.min(1, (view.w - 40) / world.W, (view.h - 40) / world.H));
  const mid = (view.w - world.W * s) / 2;
  const wide = rule && world.W * s > view.w - 2 * rule.left;
  let tx = !rule ? mid : Math.max(rule.left, mid);
  if (rule && wide && rule.focus != null) {
    const centred = Math.max(view.w - world.W * s - rule.left, view.w / 2 - rule.focus * s);
    const held = rule.start != null ? rule.left - rule.start * s : null;
    tx = Math.min(rule.left, held != null && held > centred && (rule.focus + FOCUS_HALF) * s + held <= view.w - rule.left ? held : centred);
  }
  const ty = (view.h - world.H * s) / 2;
  // The row's middle is a label's depth below the node centre; a world taller than the view stays centred.
  const row = view.h / 2 - (L.CY + 12) * s;
  const chainTy = world.H * s <= view.h ? Math.min(Math.max(row, 0), view.h - world.H * s) : ty;
  return canvas === "chain" ? { s, tx, ty: chainTy } : { s, tx: Math.max(20, tx), ty };
}

/** 100%, the current node centred across and 30% down the view. */
export const currentCam = (cx: number, cy: number, view: Size): Cam => ({ s: 1, tx: view.w / 2 - cx, ty: Math.round(view.h * 0.3) - cy });

/** The least pan that brings a world box into view with `margin` px to spare
 *  (a node focused by keyboard must be visible). Unchanged when it already is. */
export function revealCam(cam: Cam, box: { x0: number; x1: number; y0: number; y1: number }, view: Size, margin = 24): Cam {
  const fit = (lo: number, hi: number, size: number) => (lo < margin ? margin - lo : hi > size - margin ? size - margin - hi : 0);
  const dx = fit(cam.tx + box.x0 * cam.s, cam.tx + box.x1 * cam.s, view.w);
  const dy = fit(cam.ty + box.y0 * cam.s, cam.ty + box.y1 * cam.s, view.h);
  return dx || dy ? { ...cam, tx: cam.tx + dx, ty: cam.ty + dy } : cam;
}
