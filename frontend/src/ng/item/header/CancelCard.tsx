import { useEffect, useId, useState, type RefObject } from "react";
import { useFocusSoon } from "../useFocusSoon";
import { usd } from "../../../format";
import type { CancelPreview } from "../../../types";
import { Button } from "../../ui/Button";
import { Popover } from "../../ui/Popover";
import { act, cancelPreview } from "../actions";
import { taskName } from "../paths";

/** Cancel… (Decisions §14, prototype lines 43–48): what stops, what is kept,
 *  what the spend does, the MR option, a reason, then `POST /cancel`. The
 *  reason is required: the server's EndWorkItem 422s a blank one (R49.1). */
export function CancelCard({ id, anchor, onClose, onDone }: { id: string; anchor: RefObject<HTMLElement | null>; onClose: () => void; onDone: () => void }) {
  const [preview, setPreview] = useState<CancelPreview | null>(null);
  const [reason, setReason] = useState("");
  const [closeMr, setCloseMr] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reasonId = useId();
  const reasonRef = useFocusSoon<HTMLTextAreaElement>();

  useEffect(() => {
    cancelPreview(id).then((r) => (r.ok ? setPreview(r.body) : setError(r.error)));
  }, [id]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    const r = await act.cancel(id, reason.trim(), closeMr);
    setBusy(false);
    if (!r.ok) return setError(r.error);
    const mr = r.body.close_mr;
    if (mr && !mr.ok) return setError(`Cancelled. The merge request was not closed: ${mr.error ?? "the forge refused"}`);
    onDone();
  };

  const run = preview?.running;
  const rows: [string, string][] = preview
    ? [
        ["stops now", run ? `${run.task ? taskName(run.task) : run.node}${run.attempt ? `, attempt ${run.attempt}` : ""}. That attempt's work is lost.` : "Nothing is running."],
        ["keeps", `branch ${preview.kept.branch}, the worktree${preview.kept.findings ? `, ${preview.kept.findings} findings` : ", findings"}${preview.kept.threads ? `, ${preview.kept.threads} threads` : ", threads"} and the run log`],
        ["spend", `${usd(preview.spend.spent_usd)} stays on the ledger and in today's total`],
        ["afterwards", "Status CANCELLED. Archive it, or duplicate it as a new item."],
      ]
    : [];

  return (
    <Popover anchor={anchor} open onClose={onClose} role="dialog" label="Cancel this item?">
      <div className="item-card-pop">
        <h2 className="item-pop-title">Cancel this item?</h2>
        {preview ? (
          <dl className="item-facts">
            {rows.map(([k, v]) => (
              <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
            ))}
          </dl>
        ) : !error && <p className="item-muted">Reading what cancelling would do…</p>}
        {preview?.mr?.state === "open" && (
          <label className="item-check">
            <input type="checkbox" checked={closeMr} onChange={(e) => setCloseMr(e.target.checked)} /> Also close !{preview.mr.ref} on the forge
          </label>
        )}
        <label className="item-visually-hidden" htmlFor={reasonId}>Reason</label>
        <textarea id={reasonId} ref={reasonRef} className="item-input" rows={2} placeholder="Reason (required, goes in the run log)" value={reason} onChange={(e) => setReason(e.target.value)} />
        {error && <p className="item-error" role="alert">{error}</p>}
        <div className="item-actions">
          <Button variant="danger" disabled={busy || !reason.trim()} onClick={submit}>Cancel item</Button>
          <Button onClick={onClose}>Keep it going</Button>
        </div>
        <p className="item-muted">A cancelled item is not reopened. Duplicate it to try again.</p>
      </div>
    </Popover>
  );
}
