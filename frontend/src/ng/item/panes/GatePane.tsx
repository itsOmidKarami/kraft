import { REQUEST_CHANGES, sendsBack } from "../../review/finish";
import { gateName, nameOf } from "../../board/rowText";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { ChainNode } from "../../../types";
import { FileText } from "../../icons";
import { Button } from "../../ui/Button";
import { useReason } from "../../ui/Reason";
import { ADD_NOTE } from "../../ui/reasons";
import { request } from "../../http";
import { localTime } from "../../../format";
import { act } from "../actions";
import { TestsLine } from "../TestsLine";
import { byWord, gateState, isPastRound, isReviewRound, outcomeWord, roundShown, roundsOf, shownNote, threadCountLine } from "../gateRounds";
import { OUTCOME } from "../reviewOutcome";
import { rejectTarget } from "../graph";
import { roundReviewUrl } from "../../review/url";
import { totals, useDiffFiles } from "../Top";
import { runVersion, type ItemDetail } from "../useItem";
import { holdsText, isEscape, sendOnModEnter } from "../../keys";

type ThreadRow = { id: string; state: string; gate: string | null; node_id: string | null; file_path: string | null; comments?: { body: string }[] };

/** The gate's pane (Decisions §5 Gate pane, §6 Gates): the change it decides
 *  on, open threads, what it decides on, where reject goes, its status, and its
 *  History: every round with who decided; the note is read from the canvas. `picked` is the round
 *  shown (undefined: the newest); a past one describes that round, not now. */
export function GateBody({ item, version, gate, picked, onPick }: { item: ItemDetail; version: string; gate: ChainNode; picked?: number; onPick?: (n: number | undefined) => void }) {
  const past = isPastRound(item, gate.id, picked);
  // A past round's files are not the gate's now: nothing is fetched for them.
  const files = useDiffFiles(item.id, runVersion(item), item.worktree_exists === false || past);
  const [threads, setThreads] = useState<ThreadRow[]>([]);
  useEffect(() => {
    let live = true;
    request<ThreadRow[]>(`/work-items/${encodeURIComponent(item.id)}/threads`).then((r) => live && setThreads(r.status === 200 && Array.isArray(r.body) ? r.body : []));
    return () => { live = false; };
  }, [item.id, version]);
  const open = threads.filter((t) => t.state !== "resolved");
  const pending = item.pending_gate === gate.id;
  const rounds = roundsOf(item, gate.id);
  const round = roundShown(item, gate.id, picked);
  const outcome = round?.reviewer ?? null;
  // The reviewer's own concern is usually its outcome's note, shown below.
  const concerns = (item.concerns ?? []).filter((c) => c.trim() !== outcome?.note);
  const to = (round?.outcome === "rejected" && round.sent_to) || rejectTarget(item.chain_definition.nodes, gate.id);
  const t = files && totals(files);
  const who = round && byWord(round.by);
  return (
    <>
      {!past && files && files.length > 0 && (
        <>
          <p className="ip-files-head">{files.length} {files.length === 1 ? "file" : "files"} <span className="item-add">+{t!.add}</span> <span className="item-del">−{t!.del}</span></p>
          <ul className="ip-files">
            {files.slice(0, 3).map((f) => <li key={f.path}><span className="is-mono">{f.path}</span><span className="item-add">+{f.insertions}</span><span className="item-del">−{f.deletions}</span></li>)}
          </ul>
          {files.length > 3 && <p className="item-muted">+ {files.length - 3} more</p>}
        </>
      )}
      {open.length > 0 && (
        <>
          <h3 className="ip-h">Open threads · {open.length}</h3>
          <ul className="ip-notes">{open.slice(0, 3).map((x) => <li key={x.id}>{x.comments?.[0]?.body ?? x.file_path ?? "a thread"}</li>)}</ul>
        </>
      )}
      {pending && !past && concerns.length > 0 && (
        <>
          <h3 className="ip-h">Concerns · {concerns.length}</h3>
          <ul className="ip-notes">{concerns.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </>
      )}
      {/* The round's reviewer verdict. Kraft's note on it, such as the files it changed, which void its verdict, shows here; a reviewer's rejection is also the round's note, which the canvas previews (so it is not said twice). */}
      {outcome && (
        <>
          <h3 className="ip-h">Reviewer: {OUTCOME[outcome.kind].word}</h3>
          {outcome.note && outcome.note !== (round && shownNote(round)) && <p className="ip-review-note">{outcome.note}</p>}
        </>
      )}
      <dl className="item-facts ip-facts ip-gap">
        {!past && item.test_result && <div><dt>tests</dt><dd><TestsLine workItemId={item.id} result={item.test_result} /></dd></div>}
        {item.gate_artifact && pending && !past && <div><dt>decides on</dt><dd className="is-mono">{item.gate_artifact.split("/").pop()}</dd></div>}
        {past && round && <div><dt>decided</dt><dd>{outcomeWord(round)}{who ? ` · ${who}` : ""}{round.ended_at ? ` · ${localTime(round.ended_at)}` : ""}</dd></div>}
        {past && round?.requested_at && <div><dt>asked</dt><dd>{localTime(round.requested_at)}</dd></div>}
        {to && <div><dt>reject to</dt><dd>{nameOf(item.chain_definition.nodes, to)}</dd></div>}
        {!past && <div><dt>status</dt><dd>{gateState(item, gate.id).words}</dd></div>}
      </dl>
      {rounds.some((r) => r.outcome !== "pending") && (
        <>
          <h3 className="ip-h">History · {rounds.length} {rounds.length === 1 ? "round" : "rounds"}</h3>
          <ul className="ip-hist">
            {[...rounds].reverse().map((r) => {
              const by = byWord(r.by);
              const tone = ({ approved: "ok", fixed: "ok", skipped: "ok", rejected: "bad", closed: "bad", pending: "run" } as const)[r.outcome];
              const shown = r.n === round?.n;
              return (
                <li key={r.n}>
                  <button type="button" className="ip-hist-row" aria-current={shown ? "true" : undefined} onClick={() => onPick?.(r.n === rounds.length ? undefined : r.n)}>
                    <span className="ip-hist-head">
                      <span className={`round-dot is-${tone}`} aria-hidden="true" />
                      <span className="ip-hist-n">Round {r.n}{r.outcome === "pending" ? " · now" : ""}</span>
                      {r.ended_at && <span className="ip-hist-at">{localTime(r.ended_at)}</span>}
                    </span>
                    <span className="ip-hist-what">{outcomeWord(r)}{by ? ` · ${by}` : ""}</span>
                    {isReviewRound(r) && r.ended_at && <span className="ip-hist-what">{threadCountLine(r)}</span>}
                  </button>
                  {/* The picked row of a review round opens it on the review page, where the canvas's link goes. */}
                  {shown && isReviewRound(r) && r.ended_at && <Link className="link-btn ip-hist-open" to={roundReviewUrl(item.id, gate.id, r.n)}>Open in review</Link>}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </>
  );
}

/** The decision card in the gate's footer (Decisions §5: a gate can be approved from the chain). */
export function GateFooter({ item, gate, reload, onRead }: { item: ItemDetail; gate: ChainNode; reload: () => void; onRead: () => void }) {
  const navigate = useNavigate();
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const to = rejectTarget(item.chain_definition.nodes, gate.id);
  const run = async (p: ReturnType<typeof act.approve>) => {
    setBusy(true);
    setError(null);
    const r = await p;
    setBusy(false);
    if (!r.ok) return setError(r.error);
    setRejecting(false);
    reload();
  };
  const [tried, setTried] = useState(false);
  // Said after a press with no note, and gone once there is one (the review page does the same).
  const noNote = useReason(tried && !note.trim() ? ADD_NOTE : null);
  const reject = () => {
    if (!note.trim()) return setTried(true);
    return run(act.reject(item.id, gate.id, note.trim()));
  };
  // Cancel hands focus back to Request changes…, which comes back as the note goes.
  const rejectButton = useRef<HTMLButtonElement>(null);
  const refocus = useRef(false);
  useEffect(() => {
    if (!rejecting && refocus.current) rejectButton.current?.focus();
    refocus.current = false;
  }, [rejecting]);
  if (rejecting)
    return (
      // Escape closes the note, not the pane, and only when there is no text in it to lose (R11b-02).
      <div className="ip-confirm" role="group" aria-label={`${REQUEST_CHANGES}: ${gateName(item.chain_definition.nodes, gate.id)}`} onKeyDown={(e) => {
        if (!isEscape(e)) return;
        e.preventDefault();
        e.stopPropagation();
        if (!holdsText(e.target)) { refocus.current = true; setRejecting(false); }
      }}>
        <p className="ip-confirm-q">{sendsBack(false)}{to ? nameOf(item.chain_definition.nodes, to) : "the previous node"}</p>
        {/* Focus moves into the note: the Request changes… button that had it is gone (R7b-10, R10b-04). */}
        <textarea autoFocus aria-label="Why (the next agent reads it)" {...noNote.attrs} className="item-input" rows={3} value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={sendOnModEnter(reject, !busy)} />
        {error && <p className="item-error" role="alert">{error}</p>}
        <div className="item-actions">
          {noNote.note}
          <Button variant="danger" disabled={busy} onClick={reject}>{REQUEST_CHANGES}</Button>
          <Button onClick={() => { refocus.current = true; setRejecting(false); }}>Cancel</Button>
        </div>
      </div>
    );
  return (
    <div className="ip-footer">
      {item.gate_artifact && <Button onClick={onRead}><FileText size={12} aria-hidden /> Read {item.gate_artifact.split("/").pop()}</Button>}
      {/* The review page (W8): the gate review overlay first when the gate has a document (it opens only at the pending gate). */}
      <Button onClick={() => navigate(`/work-items/${encodeURIComponent(item.id)}/review?gate=${encodeURIComponent(gate.id)}${item.gate_artifact ? "&doc=1" : ""}`)}>Review gate</Button>
      <Button variant="primary" disabled={busy} onClick={() => run(act.approve(item.id, gate.id, item.review_head))}>Approve</Button>
      <Button ref={rejectButton} disabled={busy} onClick={() => setRejecting(true)}>{REQUEST_CHANGES}…</Button>
      {error && <span className="item-error" role="alert">{error}</span>}
    </div>
  );
}
