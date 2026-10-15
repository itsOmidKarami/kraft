import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import type { Cam, Size } from "./camera";
import type { ChainLayout, ChainNode } from "./layout";
import { displayName } from "../board/rowText";
import { accessibleName } from "./types";
import { useRoving } from "./useRoving";
import "./graph.css";
import { tip } from "../ui/Tooltip";

/** Overview geometry: at most `K` of the chain's width, `PAD` in from each side, glyphs on one line at `CY`. */
export const MAP = { K: 0.27, PAD: 16, CY: 18, H: 36, BOX: 12, GATE: 10 } as const;

type Props = {
  nodes: ChainNode[];
  /** The chain's own layout: the overview draws it scaled. */
  lay: ChainLayout;
  cam: Cam;
  view: Size | null;
  names: boolean;
  /** A glyph's element id, so the canvas can hand focus up to it. */
  glyphId: (id: string) => string;
  /** Where the run stands: the overview's Tab stop. */
  current?: string;
  /** Centre the canvas on a world x. */
  onPan: (x: number) => void;
  /** ↓ on a glyph: focus that node on the canvas. */
  onDown: (id: string) => void;
};

const tone = (n: ChainNode) => (n.needs || n.state === "amber" || n.state === "esc" ? "amber" : n.state === "ghost" || !n.state ? "todo" : n.state);

/** The chain overview over a long chain's canvas: every node as a small glyph in order, with its state and no label,
 *  scaled to fit the width, and a frame on the part the canvas shows. A click or a drag on it moves the canvas there. */
export function Minimap({ nodes, lay, cam, view, names, glyphId, current, onPan, onDown }: Props) {
  const k = view ? Math.min(MAP.K, (view.w - 2 * MAP.PAD) / lay.W) : MAP.K;
  const x = (wx: number) => MAP.PAD + wx * k;
  const strip = useRef<HTMLDivElement>(null);
  const roving = useRoving(current, nodes[0]?.id);
  // The world the canvas shows, clamped to the chain.
  const x0 = view ? Math.max(0, -cam.tx / cam.s) : 0;
  const x1 = view ? Math.min(lay.W, (view.w - cam.tx) / cam.s) : lay.W;
  const toWorld = (clientX: number) => (clientX - (strip.current?.getBoundingClientRect().left ?? 0) - MAP.PAD) / k;
  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    onPan(toWorld(e.clientX));
    const move = (ev: globalThis.PointerEvent) => onPan(toWorld(ev.clientX));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    const i = nodes.findIndex((n) => n.id === roving.active);
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      roving.go(nodes[Math.max(0, Math.min(nodes.length - 1, i + (e.key === "ArrowRight" ? 1 : -1)))].id);
    } else if (e.key === "ArrowDown" && roving.active) {
      e.preventDefault();
      onDown(roving.active);
    }
  };
  const half = (n: ChainNode) => (n.kind === "gate" ? MAP.GATE * 0.72 : MAP.BOX / 2) + 3;
  return (
    <div ref={strip} role="group" aria-label="Chain overview" className="minimap" style={{ height: MAP.H }} onPointerDown={onPointerDown} onKeyDown={onKeyDown}>
      <svg className="minimap-svg" width="100%" height={MAP.H} aria-hidden="true">
        {lay.items.slice(1).map((b, i) => {
          const a = lay.items[i];
          return <line key={b.node.id} x1={x(a.cx) + half(a.node)} x2={x(b.cx) - half(b.node)} y1={MAP.CY} y2={MAP.CY} className={b.node.state === "todo" ? "minimap-edge is-todo" : "minimap-edge"} />;
        })}
      </svg>
      {view && <span className="minimap-frame" style={{ left: x(x0) - 3, width: Math.max(8, (x1 - x0) * k) + 6 }} aria-hidden="true" />}
      {lay.items.map(({ node: n, cx }) => {
        const size = n.kind === "gate" ? MAP.GATE : MAP.BOX;
        return (
          <button
            key={n.id}
            id={glyphId(n.id)}
            ref={roving.ref(n.id)}
            type="button"
            tabIndex={roving.tabIndex(n.id)}
            {...tip(accessibleName({ ...n, id: names ? displayName(n) : n.id }, ""))}
            className={`minimap-node is-${n.kind} is-${tone(n)}`}
            style={{ left: x(cx) - size / 2, top: MAP.CY - size / 2, width: size, height: size }}
            onFocus={() => roving.go(n.id)}
            onClick={() => onPan(cx)}
          />
        );
      })}
    </div>
  );
}
