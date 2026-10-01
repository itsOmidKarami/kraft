import { useRef } from "react";
import { HeaderTail } from "../../shell/HeaderActions";
import { Button } from "../../ui/Button";
import { useDraft } from "./context";
import { useSelect } from "./select";
import "./draft.css";

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** The draft's state after the crumbs (Decisions §5 Editing the chain): the
 *  amber `DRAFT · N CHANGES`, which opens Review & apply, and the red
 *  `N PROBLEMS`, which steps through them. Nothing without a draft. */
export function DraftState() {
  const d = useDraft();
  const select = useSelect(d?.raw.id ?? "");
  const next = useRef(0);
  if (!d || (!d.ops.length && !d.issues.length)) return null;
  const chip = `DRAFT · ${d.changes} ${plural(d.changes, "CHANGE", "CHANGES")}`;
  const bad = `${d.issues.length} ${plural(d.issues.length, "PROBLEM", "PROBLEMS")}`;
  const onCanvas = new Set(d.shown.chain_definition.nodes.map((n) => n.id));
  const step = () => {
    const at = d.issues[next.current++ % d.issues.length];
    if (!at) return;
    // A passed added node is not on the canvas: its row is in the chain pane.
    select(onCanvas.has(at.node) ? at.node : null);
  };
  return (
    <HeaderTail>
      <button type="button" className="idr-chip" aria-label={chip} onClick={() => d.setReviewing(true)} title="Review and apply">
        {chip}
      </button>
      {d.issues.length > 0 && (
        <button type="button" className="idr-problems" aria-label={bad} onClick={step} title="Step through the problems">
          <span className="idr-full">{bad}</span>
          <span className="idr-short" aria-hidden>{d.issues.length} !</span>
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
  return (
    <Button variant="primary" aria-label="Review & apply" disabled={blocked} title={blocked ? "Fix the problems first" : `DRAFT · ${d.changes} ${plural(d.changes, "CHANGE", "CHANGES")}`} onClick={() => d.setReviewing(true)}>
      <span className="idr-full">Review &amp; apply</span>
      <span className="idr-short" aria-hidden>Review · {d.changes}</span>
    </Button>
  );
}
