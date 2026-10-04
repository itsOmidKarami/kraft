import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { ChainNode, KraftEvent } from "../../../types";
import { FileText } from "../../icons";
import { Button } from "../../ui/Button";
import { request } from "../../http";
import { act } from "../actions";
import { gateSkipped } from "../events";
import { rejectTarget } from "../graph";
import { totals, useDiffFiles } from "../Top";
import { runVersion, type ItemDetail } from "../useItem";
import { holdsText, isEscape, sendOnModEnter } from "../../keys";

type ThreadRow = { id: string; state: string; gate: string | null; node_id: string | null; file_path: string | null; comments?: { body: string }[] };

/** Who decided a passed gate and when, from its last gate_approved. */
export function gateDecision(events: KraftEvent[], gate: string) {
  const e = [...events].reverse().find((x) => x.type === "gate_approved" && (x.payload.gate ?? x.node_id) === gate);
  return e ? { by: e.payload.by === "agent" || e.payload.by === "kraft" ? "auto" : "you", at: e.created_at } : null;
}

/** The gate's pane (Decisions §5 Gate pane, §6 Gates): the change it decides
 *  on, open threads, what it decides on, where reject goes, its status. */
export function GateBody({ item, version, gate, events }: { item: ItemDetail; version: string; gate: ChainNode; events: KraftEvent[] }) {
  const files = useDiffFiles(item.id, runVersion(item), item.worktree_exists === false);
  const [threads, setThreads] = useState<ThreadRow[]>([]);
  useEffect(() => {
    let live = true;
    request<ThreadRow[]>(`/work-items/${encodeURIComponent(item.id)}/threads`).then((r) => live && setThreads(r.status === 200 && Array.isArray(r.body) ? r.body : []));
    return () => { live = false; };
  }, [item.id, version]);
  const open = threads.filter((t) => t.state !== "resolved");
  const pending = item.pending_gate === gate.id;
  const decided = gateDecision(events, gate.id);
  const to = rejectTarget(item.chain_definition.nodes, gate.id);
  const t = files && totals(files);
  return (
    <>
      {files && files.length > 0 && (
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
      <dl className="item-facts ip-facts ip-gap">
        {item.test_result && <div><dt>tests</dt><dd><TestsLine result={item.test_result} /></dd></div>}
        {item.gate_artifact && pending && <div><dt>decides on</dt><dd className="is-mono">{item.gate_artifact.split("/").pop()}</dd></div>}
        {to && <div><dt>reject to</dt><dd className="is-mono">{to}</dd></div>}
        <div><dt>status</dt><dd>{pending ? "waiting for you" : decided ? `passed · ${decided.by}` : gateSkipped(events, gate.id) ? "skipped" : item.chain_definition.nodes.findIndex((n) => n.id === gate.id) < item.chain_definition.nodes.findIndex((n) => n.id === item.current_node_id) ? "passed" : "not reached"}</dd></div>
      </dl>
    </>
  );
}

/** "✓ 3 scopes", or "✗ 1 of 3 scopes" with each red scope linked to its session's log. */
function TestsLine({ result }: { result: NonNullable<ItemDetail["test_result"]> }) {
  const n = result.scopes.length;
  if (result.passed) return <>✓ {n} {n === 1 ? "scope" : "scopes"}</>;
  const red = result.scopes.filter((s) => !s.passed);
  return (
    <>
      ✗ {red.length} of {n} {n === 1 ? "scope" : "scopes"}
      {red.map((s) => <span key={s.session_id}> · <a className="item-link is-mono" href={`/api/worker-sessions/${encodeURIComponent(s.session_id)}/log`} target="_blank" rel="noreferrer">{s.scope ?? s.command}</a></span>)}
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
  const reject = () => run(act.reject(item.id, gate.id, note.trim()));
  // Cancel hands focus back to Reject…, which comes back as the note goes.
  const rejectButton = useRef<HTMLButtonElement>(null);
  const refocus = useRef(false);
  useEffect(() => {
    if (!rejecting && refocus.current) rejectButton.current?.focus();
    refocus.current = false;
  }, [rejecting]);
  if (rejecting)
    return (
      // Escape closes the note, not the pane, and only when there is no text in it to lose (R11b-02).
      <div className="ip-confirm" role="group" aria-label={`Reject ${gate.id}`} onKeyDown={(e) => {
        if (!isEscape(e)) return;
        e.preventDefault();
        e.stopPropagation();
        if (!holdsText(e.target)) { refocus.current = true; setRejecting(false); }
      }}>
        <p className="ip-confirm-q">Reject <code>{gate.id}</code>{to && <> · goes back to <code>{to}</code></>}</p>
        {/* Focus moves into the note: the Reject… button that had it is gone (R7b-10, R10b-04). */}
        <textarea autoFocus aria-label="Why (the next agent reads it)" className="item-input" rows={3} value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={sendOnModEnter(reject, !busy && !!note.trim())} />
        {error && <p className="item-error" role="alert">{error}</p>}
        <div className="item-actions">
          <Button variant="danger" disabled={busy || !note.trim()} onClick={reject}>Reject</Button>
          <Button onClick={() => { refocus.current = true; setRejecting(false); }}>Cancel</Button>
        </div>
      </div>
    );
  return (
    <div className="ip-footer">
      {item.gate_artifact && <Button onClick={onRead}><FileText size={12} aria-hidden /> Read {item.gate_artifact.split("/").pop()}</Button>}
      {/* The review page (W8): the gate review overlay first when the gate has a document (it opens only at the pending gate). */}
      <Button onClick={() => navigate(`/work-items/${encodeURIComponent(item.id)}/review?gate=${encodeURIComponent(gate.id)}${item.gate_artifact ? "&doc=1" : ""}`)}>Review changes</Button>
      <Button variant="primary" disabled={busy} onClick={() => run(act.approve(item.id, gate.id))}>Approve</Button>
      <Button ref={rejectButton} disabled={busy} onClick={() => setRejecting(true)}>Reject…</Button>
      {error && <span className="item-error" role="alert">{error}</span>}
    </div>
  );
}
