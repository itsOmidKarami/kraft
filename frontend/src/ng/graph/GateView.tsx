import { hint } from "../ui/Tooltip";
import { FileText, NodeIcon } from "../icons";
import { NodeGlyph } from "./NodeGlyph";
import { accessibleName, CHIP_TONE, type GlyphState } from "./types";
import "./graph.css";
import { Badge } from "../ui/Badge";
import type { Rounds } from "../item/nodeGraph";
import { RoundPicker } from "./RoundPicker";
import { NotePreview } from "../ui/NotePreview";

type Reviewer = { id?: string; icon?: string; state?: GlyphState; running?: boolean; chip?: string; chipTone?: "green" | "red" | "amber"; sel?: boolean; mark?: "add" | "change"; prob?: boolean; mode?: "decides" | "checks_first" };
type Props = {
  gate: { id: string; state?: GlyphState; mark?: "add" | "change"; prob?: boolean; sel?: boolean };
  /** The gate's auto_review task: it runs on the gate's path, before the person. */
  reviewer?: Reviewer;
  message?: string;
  doc?: { label: string; onClick?: () => void };
  /** `taken`: the round shown sent work back to `id`, so the target is drawn solid; `fixed`: its reviewer repaired the worktree itself. */
  reject?: { id: string; label?: string; icon?: string; onClick?: () => void; taken?: boolean; fixed?: boolean };
  note?: string;
  /** What the round shown was decided with: its note, under the message, as a short preview. `onRead` opens the whole note. */
  said?: { note: string; at: string; n: number; onRead?: (button: HTMLElement) => void };
  /** The gate's rounds, when it has two or more, and the one picked (undefined: the newest). */
  rounds?: Rounds;
  onRound?: (n: number | undefined) => void;
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
export function GateView({ gate, reviewer: r, message, doc, reject, note, said, rounds, onRound, youSub, right = 0, onAdd, onReviewer, onGate, onBackground }: Props) {
  const ran = r && (r.state === "done" || r.state === "current");
  const gateLabel = r || onAdd ? "you" : gate.id;
  return (
    <div className="gateview" style={{ right }} onClick={(e) => !(e.target as Element).closest("button") && onBackground?.()}>
      <div className="gateview-col">
        <div className="gateview-row">
          {r && (
            <>
              <button type="button" className={`gateview-item${r.sel ? " is-sel" : ""}`} aria-label={accessibleName({ ...r, id: r.id ?? "auto_review" }, "reviewer task")} {...hint("The gate's auto_review task · runs before you decide")} onClick={onReviewer}>
                <NodeGlyph size="gv" icon={r.icon ?? "bot"} state={r.state} mark={r.mark} prob={r.prob} sel={r.sel} running={r.running} />
                <span className={`gateview-label${r.mark === "add" ? " is-add" : ""}`}>{r.id ?? "auto_review"}</span>
                <Badge tone={r.chipTone && CHIP_TONE[r.chipTone]}>{r.chip ?? (r.mode === "checks_first" ? "checks first" : r.mode === "decides" ? "decides first" : "runs first, before you")}</Badge>
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
        {said && <NotePreview key={said.n} className="gateview-said" note={said.note} onRead={said.onRead} />}
        {reject && (
          <div className="gateview-reject">
            <span className="gateview-reject-line" aria-hidden="true" />
            <span className="gateview-reject-word" aria-hidden="true">{reject.fixed ? "fixed" : reject.taken ? "rejected to" : "reject"}</span>
            {!reject.fixed && (
              <button type="button" className={`gateview-target${reject.taken ? " is-taken" : ""}`} aria-label={`${reject.taken ? "rejected" : "reject"} to ${reject.label ?? reject.id}`} {...hint("Open the target node")} onClick={reject.onClick}><NodeIcon name={reject.icon} size={14} />{reject.label ?? reject.id}</button>
            )}
          </div>
        )}
        {note && <p className="gateview-note">{note}</p>}
      </div>
      {rounds && onRound && <div className="gateview-foot" style={{ right: 12 }}><RoundPicker rounds={rounds} onPick={onRound} gate /></div>}
    </div>
  );
}
