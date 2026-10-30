import { hint } from "../ui/Tooltip";
import { FileText, NodeIcon } from "../icons";
import { NodeGlyph } from "./NodeGlyph";
import { accessibleName, type GlyphState } from "./types";
import "./graph.css";
import { Badge } from "../ui/Badge";

export const CHIP_TONE = { green: "ok", red: "bad", amber: "warn" } as const;
type Reviewer = { id?: string; icon?: string; state?: GlyphState; chip?: string; chipTone?: "green" | "red" | "amber"; sel?: boolean; mark?: "add" | "change"; prob?: boolean; mode?: "decides" | "checks_first" };
type Props = {
  gate: { id: string; state?: GlyphState; mark?: "add" | "change"; prob?: boolean; sel?: boolean };
  /** The gate's auto_review task: it runs on the gate's path, before the person. */
  reviewer?: Reviewer;
  message?: string;
  doc?: { label: string; onClick?: () => void };
  reject?: { id: string; label?: string; icon?: string; onClick?: () => void };
  note?: string;
  youSub?: string;
  /** Px the docked pane takes on the right. */
  right?: number;
  onAdd?: () => void;
  onReviewer?: () => void;
  onGate?: () => void;
  onBackground?: () => void;
};

/** A gate's node view (GateView.dc.html): reviewer → line → you, the message,
 *  the document, and the reject branch. */
export function GateView({ gate, reviewer: r, message, doc, reject, note, youSub, right = 0, onAdd, onReviewer, onGate, onBackground }: Props) {
  const ran = r && (r.state === "done" || r.state === "current");
  const gateLabel = r || onAdd ? "you" : gate.id;
  return (
    <div className="gateview" style={{ right }} onClick={(e) => !(e.target as Element).closest("button") && onBackground?.()}>
      <div className="gateview-col">
        <div className="gateview-row">
          {r && (
            <>
              <button type="button" className={`gateview-item${r.sel ? " is-sel" : ""}`} aria-label={accessibleName({ ...r, id: r.id ?? "auto_review" }, "reviewer task")} {...hint("The gate's auto_review task · runs before you decide")} onClick={onReviewer}>
                <NodeGlyph size="gv" icon={r.icon ?? "bot"} state={r.state} mark={r.mark} prob={r.prob} sel={r.sel} running={r.state === "current"} />
                <span className={`gateview-label${r.mark === "add" ? " is-add" : ""}`}>{r.id ?? "auto_review"}</span>
                <Badge tone={r.chipTone && CHIP_TONE[r.chipTone]}>{r.chip ?? "runs first, before you"}</Badge>
              </button>
              <span className={`gateview-line${ran ? " is-ran" : ""}`} aria-hidden="true" />
            </>
          )}
          {!r && onAdd && (
            <>
              <button type="button" className="gateview-item is-add" {...hint("Add an agent task that reviews this gate before you do")} onClick={onAdd}>
                <NodeGlyph kind="slot" size="gv" />
                <span className="gateview-label is-add">add a reviewer</span>
                <Badge>optional · agent task</Badge>
              </button>
              <span className="gateview-line" aria-hidden="true" />
            </>
          )}
          <button type="button" className={`gateview-item${gate.sel ? " is-sel" : ""}`} aria-label={accessibleName({ ...gate, id: gateLabel }, "gate")} onClick={onGate}>
            <NodeGlyph kind="gate" size="gv" state={gate.state} mark={gate.mark} prob={gate.prob} sel={gate.sel} />
            <span className="gateview-label is-gate">{gateLabel}</span>
            <Badge>{youSub ?? (r ? "decides after the agent" : "decides")}</Badge>
          </button>
        </div>
        <p className={`gateview-msg${message ? "" : " is-empty"}`}>{message ?? "No message yet."}</p>
        {doc && <button type="button" className="gateview-doc" onClick={doc.onClick} disabled={!doc.onClick}><FileText size={14} />{doc.label}</button>}
        {reject && (
          <div className="gateview-reject">
            <span className="gateview-reject-line" aria-hidden="true" />
            <span className="gateview-reject-word" aria-hidden="true">reject</span>
            <button type="button" className="gateview-target" aria-label={`reject to ${reject.label ?? reject.id}`} {...hint("Open the target node")} onClick={reject.onClick}><NodeIcon name={reject.icon} size={14} />{reject.label ?? reject.id}</button>
          </div>
        )}
        {note && <p className="gateview-note">{note}</p>}
      </div>
    </div>
  );
}
