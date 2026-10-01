import { LocateFixed, Minus, Plus, Scan } from "../icons";
import type { CameraMode } from "./useCamera";
import "./graph.css";

type Props = { scale: number; mode: CameraMode; onIn: () => void; onOut: () => void; onReset: () => void; onFit: () => void; onCurrent?: () => void; fitLabel?: string };

/** −, %, +, fit, current (ZoomControls.dc.html). The canvas places it. */
export function ZoomControls({ scale, mode, onIn, onOut, onReset, onFit, onCurrent, fitLabel = "Fit" }: Props) {
  return (
    <div role="group" aria-label="Zoom" className="zoom" onPointerDown={(e) => e.stopPropagation()}>
      <button type="button" className="zoom-btn" aria-label="Zoom out" title="Zoom out" onClick={onOut}><Minus size={13} /></button>
      <button type="button" className="zoom-btn zoom-pct" aria-label="Zoom to 100%" title="100%" onClick={onReset}>{Math.round(scale * 100)}%</button>
      <button type="button" className="zoom-btn" aria-label="Zoom in" title="Zoom in" onClick={onIn}><Plus size={13} /></button>
      <button type="button" className="zoom-btn" aria-label={fitLabel} title={fitLabel} aria-pressed={mode === "fit"} onClick={onFit}><Scan size={13} /></button>
      {onCurrent && <button type="button" className="zoom-btn" aria-label="Centre on the current node" title="Centre on the current node" aria-pressed={mode === "current"} onClick={onCurrent}><LocateFixed size={13} /></button>}
    </div>
  );
}
