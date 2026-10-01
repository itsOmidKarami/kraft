import { useState } from "react";
import { usd } from "../../../format";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { showToast } from "../../ui/Toast";
import { useApply } from "./apply";
import { useDraft } from "./context";
import { useSelect } from "./select";
import { lines, PASSED } from "./view";

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** Review & apply (Decisions §5): the exact change, the checks, when the run
 *  reaches it, then Apply, Back to editing or Discard draft. */
export function ReviewDialog() {
  const d = useDraft();
  return d?.reviewing && d.draft.view ? <Review /> : null;
}

function Review() {
  const d = useDraft()!;
  const apply = useApply();
  const select = useSelect(d.raw.id);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [moved, setMoved] = useState(0);
  const view = d.draft.view!;
  const n = d.changes;
  const problems = d.issues.filter((i) => !i.passed);
  const passed = d.issues.filter((i) => i.passed);
  const blocked = d.issues.length > 0;
  const adds = d.ops.flatMap((o) => (o.op === "add_node" && !o.passed ? [o] : []));
  const budget = view.checks.budget;
  const close = () => d.setReviewing(false);

  const run = async () => {
    setBusy(true);
    const o = await apply();
    setBusy(false);
    if (o.kind === "moved") setMoved(o.passed);
    else if (o.kind === "applied" || o.kind === "gone") close();
  };
  const discard = async () => {
    setBusy(true);
    const a = await d.draft.discard();
    setBusy(false);
    if (a.status === 204 || a.status === 404) {
      showToast("Draft discarded");
      close();
    }
  };

  return (
    <Dialog title={`Apply ${n} ${plural(n, "change", "changes")} to this item?`} onClose={close} className="idr-review"
      footer={asking ? (
        <>
          <span className="idr-ask">Discard this draft? It cannot be brought back.</span>
          <Button onClick={() => setAsking(false)}>Keep</Button>
          <Button className="idr-danger" disabled={busy} onClick={discard}>Discard</Button>
        </>
      ) : (
        <>
          <Button className="idr-danger idr-left" onClick={() => setAsking(true)}>Discard draft</Button>
          <Button onClick={close}>Back to editing</Button>
          <Button variant="primary" disabled={blocked || busy} onClick={run}>Apply</Button>
        </>
      )}>
      <p className="idr-sub">Only this item changes. The chain template and other items stay as they are.</p>
      {blocked && (
        <ul className="idr-probs" aria-label="Problems">
          {d.issues.map((i) => (
            <li key={`${i.index}-${i.message}`}>
              <button type="button" className="idr-prob" onClick={() => { close(); select(d.shown.chain_definition.nodes.some((x) => x.id === i.node) ? i.node : null); }}>
                <span className="is-mono">{i.node}</span> · {i.message}
              </button>
            </li>
          ))}
        </ul>
      )}
      <pre className="idr-lines" aria-label="Changes">
        {lines(d.ops).map((l) => <span key={`${l.index}-${l.text}`} className={`idr-line is-${l.tone}`}>{l.text}{"\n"}</span>)}
      </pre>
      <p className="idr-h">checked against policy</p>
      <ul className="idr-checks">
        <li className={problems.length ? "is-bad" : "is-ok"}>{problems.length ? `✕ ${problems.length} ${plural(problems.length, "problem", "problems")} to fix first` : "✓ resolves"}</li>
        <li className={passed.length ? "is-bad" : "is-ok"}>{passed.length ? `✕ ${passed.length} ${plural(passed.length, "edit", "edits")} the run has passed` : "✓ only nodes that have not run"}</li>
        <li className="is-ok">{budget.cap_usd == null ? "✓ no dollar cap on this item" : `✓ spent ${usd(budget.spent_usd ?? 0)} of the ${usd(budget.cap_usd)} budget`}</li>
      </ul>
      {moved > 0 && <p className="idr-moved" role="alert">The run moved past {moved} {plural(moved, "edit", "edits")} while you were reviewing. {PASSED}</p>}
      <p className="idr-note">{adds.length ? `The run reaches ${adds.map((a) => a.node.id).join(", ")} after ${adds[0].after}.` : "They apply as the run reaches each node."}</p>
    </Dialog>
  );
}
