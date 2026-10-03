import { useEffect, useRef, useState } from "react";
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
 *  reaches it, then Apply, Back to editing or Discard draft. Opened by Start on
 *  an item whose draft holds changes, it asks first: Apply and start, or Start
 *  without them, which leaves them in the draft. Start never applies a draft on
 *  its own, and a change it left behind is passed once the run reaches it. */
export function ReviewDialog() {
  const d = useDraft();
  return d?.reviewing && d.draft.view ? <Review /> : null;
}

function Review() {
  const d = useDraft()!;
  const apply = useApply();
  const select = useSelect(d.raw.id);
  const [asking, setAsking] = useState(false);
  // The footer swaps its buttons, so the pressed one unmounts: focus goes to Keep,
  // and back to the button that asked when Keep is pressed.
  const keep = useRef<HTMLButtonElement>(null);
  const ask = useRef<HTMLButtonElement>(null);
  const asked = useRef(false);
  useEffect(() => {
    if (asking) keep.current?.focus();
    else if (asked.current) ask.current?.focus();
    asked.current ||= asking;
  }, [asking]);
  const [busy, setBusy] = useState(false);
  const [moved, setMoved] = useState(0);
  const view = d.draft.view!;
  const n = d.changes;
  const problems = d.issues.filter((i) => !i.passed);
  const passed = d.issues.filter((i) => i.passed);
  const blocked = d.issues.length > 0;
  const overrides = d.ops.flatMap((o) => (o.op === "override" && !o.passed ? [o] : []));
  // A refused override's message starts with the field (`policy.budget_usd: ...`): the line names it, and Apply is blocked by the problem.
  const overrun = problems.filter((i) => d.ops[i.index]?.op === "override");
  const adds = d.ops.flatMap((o) => (o.op === "add_node" && !o.passed ? [o] : []));
  const budget = view.checks.budget;
  const close = () => d.setReviewing(false);
  const start = d.starting;
  const primary = useRef<HTMLButtonElement>(null);
  // Opened from an address (the peek's Start sends `?start=1`) nothing had focus, and
  // Review & apply goes once its draft is empty: either way focus goes to the header's
  // main button, not to the page (R10b-04).
  const returnTo = () => document.querySelector<HTMLElement>(".item-main-action");

  const run = async () => {
    setBusy(true);
    const o = await apply();
    setBusy(false);
    if (o.kind === "moved") setMoved(o.passed);
    else if (o.kind === "applied" || o.kind === "gone") {
      close();
      if (o.kind === "applied") start?.();
    }
  };
  const startWithout = () => { close(); start?.(); };
  // An edit the run has passed can never apply, and it holds the rest back: take it out of the draft.
  const dropPassed = async () => {
    const gone = new Set(passed.map((i) => i.index));
    setBusy(true);
    await d.draft.edit((ops) => ops.filter((_, i) => !gone.has(i)));
    setBusy(false);
    setMoved(0);
    if (gone.size === d.ops.length) return close();
    // The link that was pressed is gone with what it removed: focus the way on (R10b-04).
    requestAnimationFrame(() => primary.current?.focus());
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
    <Dialog title={start ? `Start with ${n} unapplied ${plural(n, "change", "changes")}?` : `Apply ${n} ${plural(n, "change", "changes")} to this item?`} onClose={close} className="idr-review" returnTo={returnTo}
      footer={asking ? (
        <>
          <span className="idr-ask">Discard this draft? It cannot be brought back.</span>
          <Button ref={keep} onClick={() => setAsking(false)}>Keep</Button>
          <Button className="idr-danger" disabled={busy} onClick={discard}>Discard</Button>
        </>
      ) : (
        <>
          <Button ref={ask} className="idr-danger idr-left" onClick={() => setAsking(true)}>Discard draft</Button>
          {/* Focus opens on the primary, or on Back to editing while it is blocked; never on Discard draft (R10b-05). */}
          <Button data-autofocus={blocked ? true : undefined} onClick={close}>Back to editing</Button>
          {start && <Button disabled={busy} onClick={startWithout}>Start without them</Button>}
          <Button ref={primary} data-autofocus={blocked ? undefined : true} variant="primary" disabled={blocked || busy} onClick={run}>{start ? "Apply and start" : "Apply"}</Button>
        </>
      )}>
      <p className="idr-sub">{start ? "These changes are only in the item's draft. Start does not apply them: apply them now, or start without them and they stay in the draft. " : ""}Only this item changes. The chain itself and the other items stay as they are.</p>
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
        <li className={passed.length ? "is-bad" : "is-ok"}>
          {passed.length ? `✕ ${passed.length} ${plural(passed.length, "edit", "edits")} the run has passed, which can no longer apply` : "✓ only nodes that have not run"}
          {passed.length > 0 && <> <button type="button" className="item-link" disabled={busy} onClick={dropPassed}>Remove {passed.length === 1 ? "it" : "them"} from the draft</button></>}
        </li>
        {overrides.length > 0 && (overrun.length
          ? overrun.map((i) => <li key={i.index} className="is-bad">✕ {i.message}</li>)
          : <li className="is-ok">✓ within policy maxima</li>)}
        {(view.checks.added ?? []).map((a) => (
          <li key={a.op} className="is-ok">
            ✓ {a.node}: {a.harnesses.length === 0 ? "runs no agent" : `${plural(a.harnesses.length, "harness", "harnesses")} ${a.harnesses.join(", ")} ${plural(a.harnesses.length, "is", "are")} allowed`} · {a.estimate_usd == null ? "no estimate yet" : `≈ +${usd(a.estimate_usd)} (avg of ${a.estimate_runs ?? 1} ${plural(a.estimate_runs ?? 1, "run", "runs")})`}
          </li>
        ))}
        <li className="is-ok">{budget.cap_usd == null ? "✓ no dollar cap on this item" : `✓ spent ${usd(budget.spent_usd ?? 0)} of the ${usd(budget.cap_usd)} budget`}</li>
      </ul>
      {moved > 0 && <p className="idr-moved" role="alert">The run moved past {moved} {plural(moved, "edit", "edits")} while you were reviewing. {PASSED}</p>}
      <p className="idr-note">{adds.length ? `The run reaches ${adds.map((a) => a.node.id).join(", ")} after ${adds[0].after}.` : "They apply as the run reaches each node."}</p>
    </Dialog>
  );
}
