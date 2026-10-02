import { elapsed, tokens, usd } from "../../../format";
import type { WorkerSession, WorkItemDocument } from "../../../types";
import { FileText } from "../../icons";
import { lookWord, sessionLook } from "../nodeGraph";
import type { ItemDetail } from "../useItem";

/** ‹ attempt n of m › above every tab: which session the tabs show (Decisions §6 Attempts). */
export function AttemptSwitcher({ sessions, at, onAt, now }: { sessions: WorkerSession[]; at: WorkerSession; onAt: (attempt: number) => void; now: number }) {
  const i = sessions.indexOf(at);
  const look = sessionLook(at, now);
  return (
    <div className="ip-attempts">
      <span className="ip-attempt-box">
        <button type="button" className="ip-attempt-btn" aria-label="Earlier attempt" disabled={i <= 0} onClick={() => onAt(sessions[i - 1].attempt)}>‹</button>
        <span>attempt {at.attempt} of {sessions.at(-1)!.attempt}{at.round ? ` · round ${at.round + 1}` : ""}</span>
        <button type="button" className="ip-attempt-btn" aria-label="Later attempt" disabled={i >= sessions.length - 1} onClick={() => onAt(sessions[i + 1].attempt)}>›</button>
      </span>
      <span className={`ip-attempt-state${look.running ? " is-live" : look.state === "failed" ? " is-bad" : ""}`}>{lookWord(look)}</span>
    </div>
  );
}

const fact = (k: string, v: React.ReactNode) => (v == null || v === "" ? null : <div key={k}><dt>{k}</dt><dd>{v}</dd></div>);

/** Overview: what ran and its result, with the documents this attempt wrote (Decisions §6 Documents). */
export function TaskOverview({ path, s, docs, onDoc }: { path: string; s: WorkerSession; docs: WorkItemDocument[]; onDoc: (d: WorkItemDocument) => void }) {
  const mine = docs.filter((d) => d.worker_session_id === s.id || (d.hook_point === path && d.attempt === s.attempt));
  return (
    <>
      <dl className="item-facts ip-facts">
        {fact("status", s.status.replaceAll("_", " "))}
        {fact("kind", s.model ? "agent" : null)}
        {fact("harness", s.harness && <span className="is-mono">{s.harness}</span>)}
        {fact("model", s.model && <span className="is-mono">{s.model}</span>)}
        {fact("path", <span className="is-mono">{path}</span>)}
        {fact("ran", s.wall_ms != null ? elapsed(s.wall_ms) : null)}
      </dl>
      <h3 className="ip-h">Result</h3>
      <dl className="item-facts ip-facts">
        {fact("tokens", s.tokens_in != null ? tokens((s.tokens_in ?? 0) + (s.tokens_out ?? 0)) : null)}
        {fact("cost", s.cost_usd != null ? usd(s.cost_usd, true, s.cost_estimated) : null)}
      </dl>
      <h3 className="ip-h">Documents</h3>
      {mine.length ? (
        <ul className="ip-list">
          {mine.map((d) => (
            <li key={d.document_id}><button type="button" className="ip-row" onClick={() => onDoc(d)}><FileText size={12} aria-hidden /> <span>{d.title}</span><span className="ip-row-meta">{d.kind}</span></button></li>
          ))}
        </ul>
      ) : <p className="item-muted">This attempt wrote no documents.</p>}
    </>
  );
}

/** Input: what the API keeps of what went in (Kraft-jmofl: no prompt, no inputs list). */
export function TaskInput({ item, s, current }: { item: ItemDetail; s: WorkerSession; current: boolean }) {
  return (
    <dl className="item-facts ip-facts">
      {fact("head", s.head_sha && <span className="is-mono">{s.head_sha.slice(0, 10)}</span>)}
      {fact("round", s.round ? `fix loop round ${s.round + 1}: carries the findings of round ${s.round}` : "first pass")}
      {fact("thread", s.thread > 1 ? `escalation thread ${s.thread}` : null)}
      {fact("steer", current ? item.pending_steer_context : null)}
    </dl>
  );
}

/** Output: the attempt's result, its summary document, the node's concerns. */
export function TaskOutput({ item, s, docs, onDoc }: { item: ItemDetail; s: WorkerSession; docs: WorkItemDocument[]; onDoc: (d: WorkItemDocument) => void }) {
  if (["running", "pending"].includes(s.status)) return <p className="item-muted">Still running. Output is written when the task finishes.</p>;
  const summary = s.session_summary_ref && docs.find((d) => d.path === s.session_summary_ref);
  const judged = item.judge_stop_note?.filter((j) => j.node_id === s.node_id) ?? [];
  return (
    <>
      <dl className="item-facts ip-facts">
        {fact("result", s.status.replaceAll("_", " "))}
        {fact("summary", summary ? <button type="button" className="item-link is-strong" onClick={() => onDoc(summary)}>{summary.title}</button> : s.session_summary_ref && <span className="is-mono">{s.session_summary_ref}</span>)}
      </dl>
      {judged.map((j, i) => (
        <section key={i}>
          <h3 className="ip-h">Findings the judge stopped chasing</h3>
          <p className="item-muted">{j.reasoning}</p>
          <ul className="ip-findings">{j.findings.map((f, k) => <li key={k}><span className="is-mono">{f.severity}</span> {f.file ? `${f.file}${f.line ? `:${f.line}` : ""} · ` : ""}{f.message}</li>)}</ul>
        </section>
      ))}
    </>
  );
}

/** Config: the attempt's own values, read-only (✎ is the item draft's, W11; the rest is Kraft-jmofl). */
export function TaskConfig({ path, s }: { path: string; s: WorkerSession }) {
  return (
    <dl className="item-facts ip-facts">
      {fact("path", <span className="is-mono">{path}</span>)}
      {fact("model", s.model && <span className="is-mono">{s.model}</span>)}
      {fact("attempt", String(s.attempt))}
      {fact("head", s.head_sha && <span className="is-mono">{s.head_sha.slice(0, 10)}</span>)}
    </dl>
  );
}
