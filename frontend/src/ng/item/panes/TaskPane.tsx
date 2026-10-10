import { elapsed, tokenText, tokenTip, usd } from "../../../format";
import type { TaskProgress, WorkerSession, WorkItemDocument } from "../../../types";
import { allDone } from "../../board/rowText";
import { FileText } from "../../icons";
import { Menu, type MenuItem } from "../../ui/Menu";
import { lookWord, sessionLook } from "../nodeGraph";
import type { ItemDetail } from "../useItem";

/** The attempt every tab shows, as a menu in the pane's subtitle (Decisions §6 Attempts): a pill
 *  reading "attempt n of m", one row per session, newest first (`inRound`: the sessions of one fix-loop
 *  round, counted from 1). On the escalation each session is
 *  a turn of its thread, so it reads "turn n of m" as the Thread tab counts them, not
 *  "attempt 5 of 5" beside "5 turns" (R10b-06). */
export function AttemptMenu({ sessions, at, onAt, now, turns, inRound }: { sessions: WorkerSession[]; at: WorkerSession; onAt: (attempt: number) => void; now: number; turns?: boolean; inRound?: boolean }) {
  const threads = new Set(sessions.map((s) => s.thread)).size > 1;
  const items = [...sessions].reverse().map((s): MenuItem => {
    const look = sessionLook(s, now);
    const tone = look.running ? "is-live" : look.state === "failed" ? "is-bad" : look.state === "done" ? "is-ok" : "";
    return {
      // `Menu` keys its rows by label: an attempt number is unique, a turn is unique within its thread.
      label: turns ? `Turn ${turnOf(sessions, s).n}${threads ? ` · thread ${s.thread}` : ""}` : `Attempt ${inRound ? sessions.indexOf(s) + 1 : s.attempt}`,
      hint: s.status === "pending" ? "not run yet" : look.running ? look.meta : [lookWord(look), s.wall_ms != null ? elapsed(s.wall_ms) : null].filter(Boolean).join(" · "),
      icon: <span className={`ip-pill-dot ${tone}`} />,
      checked: s === at,
      onSelect: () => onAt(s.attempt),
    };
  });
  // In a fix-loop round the count is the round's own: the retries of this one task in it.
  const text = turns ? turnWords(sessions, at) : inRound ? `attempt ${sessions.indexOf(at) + 1} of ${sessions.length}` : `attempt ${at.attempt} of ${sessions.at(-1)!.attempt}${at.round ? ` · round ${at.round + 1}` : ""}`;
  return <Menu label={turns ? "Turns" : "Attempts"} triggerClass="ip-pill" trigger={<>{text}<span className="ip-pill-caret" aria-hidden>▾</span></>} items={items} />;
}

/** A session's place among its own thread's sessions, as the Thread tab counts them. */
function turnOf(sessions: WorkerSession[], at: WorkerSession) {
  const mine = sessions.filter((s) => s.thread === at.thread);
  return { n: mine.indexOf(at) + 1, of: mine.length, many: mine.length < sessions.length };
}

/** "turn 2 of 3": the escalation's session among its own thread's, as the Thread tab counts
 *  them ("thread 2 · 1 turn"), with "· thread 2" once there is more than one. */
function turnWords(sessions: WorkerSession[], at: WorkerSession): string {
  const { n, of, many } = turnOf(sessions, at);
  return `turn ${n} of ${of}${many ? ` · thread ${at.thread}` : ""}`;
}

const fact = (k: string, v: React.ReactNode) => (v == null || v === "" ? null : <div key={k}><dt>{k}</dt><dd>{v}</dd></div>);

const subTasks = (n: number) => `${n} sub-task${n === 1 ? "" : "s"}`;

/** The plan task's place in its plan (`item.progress`), in a few words: what the fact row reads. */
function progressWords(p: TaskProgress, running: boolean): string {
  if (allDone(p)) return `${p.total} of ${subTasks(p.total)} · done`;
  return running ? `${p.current} of ${p.total} · ${p.title}` : `${p.current} of ${subTasks(p.total)}`;
}

/** The plan's sub-tasks under the plan task's facts: a bar, then one row each with its state and commit. */
function SubTasks({ tasks, docs, onDoc }: { tasks: NonNullable<TaskProgress["tasks"]>; docs: WorkItemDocument[]; onDoc: (d: WorkItemDocument) => void }) {
  const plan = docs.find((d) => d.attachment_kind === "plan") ?? docs.find((d) => d.kind === "plan");
  const done = tasks.filter((t) => t.state === "done").length;
  return (
    <>
      <div className="ip-progress-head">
        <h3 className="ip-h">Progress · {subTasks(tasks.length)}</h3>
        {plan && <button type="button" className="item-link" onClick={() => onDoc(plan)}>{plan.path.split("/").at(-1)} ↗</button>}
      </div>
      <div className="ip-progress-bar" aria-hidden><span style={{ width: `${(done / tasks.length) * 100}%` }} /></div>
      <ol className="ip-subtasks" aria-label="Sub-tasks">
        {tasks.map((t) => (
          <li key={t.n} className={`ip-subtask is-${t.state}`} aria-current={t.state === "current" ? "step" : undefined}>
            <span className="ip-subtask-dot" role="img" aria-label={t.state} />
            <span className="ip-subtask-n is-mono">{t.n}</span>
            <span className="ip-subtask-title">{t.title}</span>
            <span className="ip-subtask-meta">{t.state === "current" ? "current" : t.state === "done" && t.sha ? <span className="is-mono">{t.sha}</span> : null}</span>
          </li>
        ))}
      </ol>
    </>
  );
}

/** Overview: what ran and its result, with the documents this attempt wrote (Decisions §6 Documents).
 *  On the plan task, `progress` is the plan's sub-tasks, whichever attempt is shown: it describes
 *  the plan, not a session; `running` is whether the task's newest session runs. */
export function TaskOverview({ path, s, docs, onDoc, progress, running }: { path: string; s: WorkerSession; docs: WorkItemDocument[]; onDoc: (d: WorkItemDocument) => void; progress?: TaskProgress | null; running?: boolean }) {
  const mine = docs.filter((d) => d.worker_session_id === s.id || (d.hook_point === path && d.attempt === s.attempt));
  return (
    <>
      <dl className="item-facts ip-facts">
        {fact("status", s.status.replaceAll("_", " "))}
        {fact("progress", progress ? progressWords(progress, !!running) : null)}
        {fact("kind", s.model ? "agent" : null)}
        {fact("harness", s.harness && <span className="is-mono">{s.harness}</span>)}
        {fact("model", s.model && <span className="is-mono">{s.model}</span>)}
        {fact("path", <span className="is-mono">{path}</span>)}
        {fact("ran", s.wall_ms != null ? elapsed(s.wall_ms) : null)}
      </dl>
      {progress?.tasks?.length ? <SubTasks tasks={progress.tasks} docs={docs} onDoc={onDoc} /> : null}
      <h3 className="ip-h">Result</h3>
      <dl className="item-facts ip-facts">
        {fact("tokens", s.tokens_in != null ? <span data-tip={tokenTip(s)}>{tokenText(s)}</span> : null)}
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

/** Output: the attempt's result, the document it produced (a task's `produces`, its real output: the
 *  session note only says where it is), its summary document, the node's concerns. */
export function TaskOutput({ item, s, docs, onDoc, produces, onProduced }: { item: ItemDetail; s: WorkerSession; docs: WorkItemDocument[]; onDoc: (d: WorkItemDocument) => void; produces?: string; onProduced?: () => void }) {
  if (["running", "pending"].includes(s.status)) return <p className="item-muted">Still running. Output is written when the task finishes.</p>;
  const summary = s.session_summary_ref && docs.find((d) => d.path === s.session_summary_ref);
  const judged = item.judge_stop_note?.filter((j) => j.node_id === s.node_id) ?? [];
  return (
    <>
      <dl className="item-facts ip-facts">
        {fact("result", s.status.replaceAll("_", " "))}
        {fact("wrote", produces && <button type="button" className="item-link is-strong" onClick={onProduced}>{produces.replaceAll("_", " ")}</button>)}
        {fact("summary", summary ? <button type="button" className={`item-link${produces ? "" : " is-strong"}`} onClick={() => onDoc(summary)}>{summary.title}</button> : s.session_summary_ref && <span className="is-mono">{s.session_summary_ref}</span>)}
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
