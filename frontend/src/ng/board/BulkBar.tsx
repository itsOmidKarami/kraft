import { useState } from "react";
import { shortId } from "../../format";
import type { WorkItem } from "../../types";
import { pausable } from "../item/status";
import { showToast } from "../ui/Toast";
import { CANCEL_WINDOW_MS, cancelLater, items, sendBulk, useBulk, VERB } from "./bulk";

const ended = (i: WorkItem) => i.display_status === "done" || i.display_status === "cancelled";

/** The selection bar (AreaBoard 92–94, Decisions §14 Board): Pause the running
 *  ones, Cancel… anything not ended (inline confirm with a reason, then a
 *  window with Undo, R3), Archive the ended ones. A bulk answer with failures
 *  takes the bar's place, one line per failure. */
export function BulkBar({ checked, byId, offline, onChecked }: { checked: WorkItem[]; byId: Record<string, WorkItem>; offline: boolean; onChecked: (ids: string[]) => void }) {
  const last = useBulk((s) => s.last);
  const [confirm, setConfirm] = useState(false);
  const [reason, setReason] = useState("");
  const pause = checked.filter(pausable);
  const cancel = checked.filter((i) => !ended(i));
  const archive = checked.filter(ended);
  // Cleared before the send; the board checks the failed ones again from the answer.
  const send = (action: "pause" | "archive", list: WorkItem[]) => {
    onChecked([]);
    void sendBulk(action, list.map((i) => i.id));
  };

  if (last) {
    const failed = "results" in last ? last.results.filter((r) => !r.ok) : [];
    const title = (id: string) => byId[id]?.title ?? shortId(id);
    return (
      <div className="bulk-wrap">
        <div className="bulk-bar bulk-results" role="alert">
          <div className="bulk-results-body">
            <b>{"results" in last ? `${last.ids.length - failed.length} of ${items(last.ids.length)} ${VERB[last.action]}` : `Nothing ${VERB[last.action]}: ${last.error}`}</b>
            {failed.length > 0 && (
              <ul>
                {failed.map((r) => <li key={r.id}><span className="bulk-fail-title">{title(r.id)}</span> <span className="bulk-fail-id">{byId[r.id]?.bead_id || shortId(r.id)}</span> · {r.error}</li>)}
              </ul>
            )}
          </div>
          <button type="button" className="btn btn-secondary" onClick={() => useBulk.getState().set(null)}>Dismiss</button>
        </div>
      </div>
    );
  }
  if (!checked.length) return null;

  const doCancel = () => {
    const ids = cancel.map((i) => i.id);
    const undo = cancelLater(ids, reason.trim());
    setConfirm(false);
    setReason("");
    onChecked([]);
    showToast(`Cancelling ${items(ids.length)}…`, { ms: CANCEL_WINDOW_MS, action: { label: "Undo", run: () => void (undo() && onChecked(checked.map((i) => i.id))) } });
  };

  return (
    <div className="bulk-wrap">
      <div className="bulk-bar" role="toolbar" aria-label="Selected work items">
        <span>{checked.length} selected</span>
        {confirm ? (
          <>
            <span className="bulk-confirm-text">Cancel {items(cancel.length)}? Running attempts stop and are lost. Branches, findings and spend are kept.</span>
            <input className="bulk-reason" aria-label="Reason (required, goes in the run log)" placeholder="Reason (required)" value={reason} autoFocus onChange={(e) => setReason(e.target.value)} />
            <button type="button" className="btn btn-danger" disabled={!reason.trim() || offline} onClick={doCancel}>Cancel {cancel.length}</button>
            <button type="button" className="btn btn-secondary" onClick={() => setConfirm(false)}>Keep</button>
          </>
        ) : (
          <>
            {pause.length > 0 && <button type="button" className="btn btn-secondary" disabled={offline} onClick={() => send("pause", pause)}>‖ Pause {pause.length}</button>}
            {cancel.length > 0 && <button type="button" className="btn btn-danger" disabled={offline} onClick={() => setConfirm(true)}>Cancel {cancel.length}…</button>}
            <button
              type="button"
              className="btn btn-primary"
              disabled={!archive.length || offline}
              title={archive.length ? "Archive the finished ones" : "Only done items can be archived"}
              onClick={() => send("archive", archive)}
            >
              Archive{archive.length ? ` ${archive.length}` : ""}
            </button>
          </>
        )}
        <button type="button" className="bulk-clear" onClick={() => { setConfirm(false); onChecked([]); }}>Clear</button>
      </div>
    </div>
  );
}
