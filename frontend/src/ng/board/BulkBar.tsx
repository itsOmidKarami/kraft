import { useEffect, useState } from "react";
import { shortId } from "../../format";
import type { WorkItem } from "../../types";
import { admits, headerState, pausable, resumable, retryable } from "../item/status";
import { useReason } from "../ui/Reason";
import { ARCHIVE_ONLY_ENDED } from "../ui/reasons";
import { showToast } from "../ui/Toast";
import { UNDO_WINDOW_MS } from "../later";
import { cancelLater, items, sendBulk, useBulk, VERB } from "./bulk";
import { LinkButton } from "../ui/LinkButton";

const ended = (i: WorkItem) => i.display_status === "done" || i.display_status === "cancelled";

/** The selection bar (AreaBoard 92–94, Decisions §14 Board): Pause what `/pause`
 *  takes (`pausable`: running and waiting ones, and queued or blocked ones when
 *  the server's `actions` say it takes those too), Resume the paused ones that
 *  have begun and Retry the stopped ones (each the item page's own call, one per
 *  item; the count on the button is how many of the selection take it), Cancel… anything not ended (inline confirm with a reason, then a
 *  window with Undo, R3), Archive the ended ones. A bulk answer with failures
 *  takes the bar's place, one line per failure. */
export function BulkBar({ checked, byId, offline, onChecked }: { checked: WorkItem[]; byId: Record<string, WorkItem>; offline: boolean; onChecked: (ids: string[]) => void }) {
  const last = useBulk((s) => s.last);
  const [confirm, setConfirm] = useState(false);
  const [reason, setReason] = useState("");
  // Nothing selected any more (Escape, Clear, a send): the confirm does not wait for the next selection.
  useEffect(() => { if (!checked.length) setConfirm(false); }, [checked.length]);
  const pause = checked.filter(pausable);
  // Each item only where the server's `actions` admit its door, the old reading kept for a server that sends none.
  const cancel = checked.filter((i) => admits(i, "cancel", !ended(i)));
  const archive = checked.filter((i) => admits(i, "archive", ended(i)));
  const resume = checked.filter(resumable);
  // Where the item page itself leads with Retry: not a gate or a question, which have their own way on.
  const retry = checked.filter((i) => retryable(i) && headerState(i).main === "retry");
  // A selection has started, so the reason shows rather than waits for a press.
  const why = useReason(archive.length ? null : ARCHIVE_ONLY_ENDED);
  // Cleared before the send; the board checks the failed ones again from the answer.
  const send = (action: "pause" | "archive" | "resume" | "retry", list: WorkItem[]) => {
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
    showToast(`Cancelling ${items(ids.length)}…`, { ms: UNDO_WINDOW_MS, action: { label: "Undo", run: () => void (undo() && onChecked(checked.map((i) => i.id))) } });
  };

  return (
    <div className="bulk-wrap">
      <div className="bulk-bar" role="toolbar" aria-label="Selected work items" onKeyDown={(e) => { if (confirm && e.key === "Escape") { e.preventDefault(); setConfirm(false); } }}>
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
            {resume.length > 0 && <button type="button" className="btn btn-secondary" disabled={offline} onClick={() => send("resume", resume)}>Resume {resume.length}</button>}
            {retry.length > 0 && <button type="button" className="btn btn-secondary" disabled={offline} onClick={() => send("retry", retry)}>Retry {retry.length}</button>}
            {cancel.length > 0 && <button type="button" className="btn btn-danger" disabled={offline} onClick={() => setConfirm(true)}>Cancel {cancel.length}…</button>}
            {why.note}
            <button
              type="button"
              className={`btn ${archive.length ? "btn-primary" : "btn-secondary"}`}
              disabled={!archive.length || offline}
              {...why.attrs}
              onClick={() => send("archive", archive)}
            >
              Archive{archive.length ? ` ${archive.length}` : ""}
            </button>
          </>
        )}
        <LinkButton onClick={() => { setConfirm(false); onChecked([]); }}>Clear</LinkButton>
      </div>
    </div>
  );
}
