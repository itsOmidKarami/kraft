import { useRef } from "react";
import { useNavigate } from "react-router-dom";
import { HeaderTail } from "../../shell/HeaderActions";
import { Button } from "../../ui/Button";
import { usePaneMemory } from "../Workspace";
import { placeUrl } from "../url";
import { useDraft } from "./context";
import "./draft.css";

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** The draft's state after the crumbs (Decisions §5 Editing the chain): the
 *  amber `DRAFT · N CHANGES`, which opens Review & apply, and the red
 *  `N PROBLEMS`, which steps through them. Nothing without a draft. */
export function DraftState() {
  const d = useDraft();
  const navigate = useNavigate();
  const setPane = usePaneMemory((s) => s.setPane);
  const next = useRef(0);
  if (!d || (!d.ops.length && !d.issues.length)) return null;
  const onCanvas = new Set(d.shown.chain_definition.nodes.map((n) => n.id));
  const step = () => {
    const at = d.issues[next.current++ % d.issues.length];
    if (!at) return;
    setPane({ open: true, userCollapsed: false });
    // A passed added node is not on the canvas: its row is in the chain pane.
    navigate(placeUrl(d.raw.id, { sel: onCanvas.has(at.node) ? { kind: "node", node: at.node } : { kind: "chain" } }));
  };
  return (
    <HeaderTail>
      <button type="button" className="idr-chip" onClick={() => d.setReviewing(true)} title="Review and apply">
        DRAFT · {d.changes} {plural(d.changes, "CHANGE", "CHANGES")}
      </button>
      {d.issues.length > 0 && (
        <button type="button" className="idr-problems" onClick={step} title="Step through the problems">
          {d.issues.length} {plural(d.issues.length, "PROBLEM", "PROBLEMS")}
        </button>
      )}
    </HeaderTail>
  );
}

/** **Review & apply**, rendered inside the header's actions: live with a draft, disabled while it has problems or an op the run has passed. */
export function ReviewButton() {
  const d = useDraft();
  if (!d || !d.ops.length) return null;
  const blocked = d.issues.length > 0;
  return <Button variant="primary" disabled={blocked} title={blocked ? "Fix the problems first" : undefined} onClick={() => d.setReviewing(true)}>Review &amp; apply</Button>;
}
