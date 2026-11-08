import { NodeIcon, Siren, X } from "../icons";
import type { GlyphKind, GraphItem } from "./types";
import "./graph.css";

/** Box and gate sizes in px, from NodeGlyph.dc.html `SIZES`. */
export const SIZES = {
  sm: { box: 30, gate: 16, icon: 12, r: 8 },
  md: { box: 44, gate: 22, icon: 17, r: 11 },
  lg: { box: 54, gate: 26, icon: 20, r: 13 },
  gv: { box: 46, gate: 32, icon: 17, r: 11 },
} as const;
export type GlyphSize = keyof typeof SIZES;

type Props = Omit<GraphItem, "id" | "label" | "meta" | "metaTone"> & { kind?: GlyphKind; size?: GlyphSize; sel?: boolean };

/** One glyph for every node and task, on every canvas and strip. Decoration:
 *  the button around it carries the accessible name. */
export function NodeGlyph({ kind = "exec", size = "md", state = "plain", sel, prob, mark, capped, skipped, attempt, attemptStopped, esc, running, paused, subagents, unfinished, icon, taskKind }: Props) {
  const Z = SIZES[size];
  const gate = kind === "gate";
  // Badges only on boxes big enough to carry them (the prototype's `big`).
  const badges = Z.box >= 40;
  const off = gate ? Math.round((Z.box - Z.gate * 1.41) / 2) - 4 : -7;
  const pill = badges && !!attempt && attempt >= 2;
  const cls = ["glyph-box", `is-${state}`, running && !paused && "is-running", mark && `mark-${mark}`, sel ? "is-sel" : prob && "is-prob", capped && "is-capped", skipped && "is-skipped"].filter(Boolean).join(" ");
  return (
    <span className={`glyph glyph-${kind}`} style={{ width: Z.box, height: Z.box }} aria-hidden="true">
      {gate ? (
        <span className={cls} style={{ width: Z.gate, height: Z.gate, borderRadius: Math.round(Z.gate * 0.27) }}>
          {(state === "done" || state === "current") && <span className="glyph-light" style={{ width: Math.max(5, Math.round(Z.gate * 0.27)) }} />}
        </span>
      ) : (
        <span className={cls} style={{ borderRadius: Z.r }}>
          {kind === "slot" ? <span style={{ fontSize: Z.icon - 1 }}>+</span> : <NodeIcon name={icon ?? (kind === "group" ? "layers" : undefined)} kind={taskKind} size={Z.icon} />}
        </span>
      )}
      {badges && prob && !pill && <span className="glyph-prob" style={{ top: off, right: off }}>!</span>}
      {pill && <span className={`glyph-att${attemptStopped ? " is-stopped" : ""}`} style={{ right: gate ? off : -11 }}>×{attempt}</span>}
      {/* Its sub-agents: how many run, and a red cross when one did not finish, as one badge in the corner. */}
      {badges && (!!subagents || !!unfinished) && (
        <span className={`glyph-corner glyph-subagents${unfinished ? (subagents ? " has-bad" : " is-bad") : ""}`} style={{ top: off, left: off }}>
          {!!subagents && subagents}
          {!!unfinished && <span className="glyph-subagents-x"><X size={9} strokeWidth={3.5} /></span>}
        </span>
      )}
      {badges && esc && <span className="glyph-corner glyph-esc" style={{ bottom: off, left: off }}><Siren size={9} /></span>}
      {badges && paused && <span className="glyph-paused">‖</span>}
    </span>
  );
}
