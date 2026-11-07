import { useState } from "react";
import type { Subagent } from "../../../types";
import { elapsed, tokens } from "../../../format";
import { byState, PROMPT_NOT_SHOWN, STATUS_WORDS, statusTone, subagentSaid } from "../subagents";
import { useSubagentDetail } from "../useSubagents";

/** The prompt behind a disclosure, fetched whole when it is opened: the list carries none. */
function Prompt({ workItemId, sessionId, agent }: { workItemId: string; sessionId: string; agent: string }) {
  const { detail, load } = useSubagentDetail(workItemId, sessionId, agent);
  return (
    <details className="ip-subagent-prompt" onToggle={(e) => { if (e.currentTarget.open) load(); }}>
      <summary>Prompt</summary>
      {detail === null ? <p className="item-muted">Reading…</p>
        : detail === "failed" ? <p className="item-muted">Could not read this sub-agent's prompt.</p>
        : detail.prompt ? <pre className="ip-prompt">{detail.prompt}</pre>
        : <p className="item-muted">{PROMPT_NOT_SHOWN}</p>}
    </details>
  );
}

/** A task's sub-agents (Overview), by state: Running, Did not finish, and Finished folded away until it is opened
 *  (open at once when it is all there is). A row opens the task's log filtered to it. */
export function SubagentSection({ workItemId, sessionId, list, onOpen }: { workItemId: string; sessionId: string; list: Subagent[] | null; onOpen: (agent: string) => void }) {
  // Once Finished has been open or shut, it stays that way: a new sub-agent arriving does not fold it under the reader.
  const [finishedOpen, setFinishedOpen] = useState<boolean | null>(null);
  if (!list?.length) return null;
  const { running, unfinished, finished } = byState(list);
  // A group says what its rows are, so only the unfinished ones carry their own status word.
  const row = (a: Subagent, withStatus: boolean) => {
    const said = subagentSaid(a);
    return (
      <li key={a.id} className="ip-subagent-row">
        <button type="button" className="ip-subagent-open" onClick={() => onOpen(a.id)}>
          <span className={`ip-pill-dot ${statusTone(a.status)}`} aria-hidden />
          <span className="ip-subagent-name" title={a.description}>{a.description}</span>
          <span className="ip-subagent-end">
            {withStatus && <span className={`ip-subagent-st ${statusTone(a.status)}`}>{STATUS_WORDS[a.status]}</span>}
            {a.duration_ms != null && <span>{elapsed(a.duration_ms)}</span>}
          </span>
          <span className="ip-subagent-meta">
            {a.type && <span className="is-mono">{a.type}</span>}
            <span className="ip-subagent-stats">
              {a.tokens != null && <span>{tokens(a.tokens)} tokens</span>}
              {a.tool_uses != null && <span>{a.tool_uses} tool use{a.tool_uses === 1 ? "" : "s"}</span>}
            </span>
          </span>
          <span className="item-visually-hidden"> — open its log</span>
        </button>
        {said && <p className="ip-subagent-said">{said}</p>}
        <Prompt workItemId={workItemId} sessionId={sessionId} agent={a.id} />
      </li>
    );
  };
  return (
    <section className="ip-subagents">
      <h3 className="ip-h">Sub-agents</h3>
      <div className="ip-subagent-groups">
        {running.length > 0 && (
          <div>
            <h4 className="ip-subagent-head"><span className="ip-pill-dot is-live" aria-hidden />Running <span className="ip-subagent-n">{running.length}</span></h4>
            <ul className="ip-subagent-list" aria-label="Running sub-agents">{running.map((a) => row(a, false))}</ul>
          </div>
        )}
        {unfinished.length > 0 && (
          <div>
            <h4 className="ip-subagent-head"><span className="ip-pill-dot is-bad" aria-hidden />Did not finish <span className="ip-subagent-n">{unfinished.length}</span></h4>
            <ul className="ip-subagent-list" aria-label="Sub-agents that did not finish">{unfinished.map((a) => row(a, true))}</ul>
          </div>
        )}
        {finished.length > 0 && (
          <details className="ip-subagent-fold" open={finishedOpen ?? (!running.length && !unfinished.length)} onToggle={(e) => setFinishedOpen(e.currentTarget.open)}>
            <summary className="ip-subagent-head"><span className="ip-pill-dot is-ok" aria-hidden />Finished <span className="ip-subagent-n">{finished.length}</span></summary>
            <ul className="ip-subagent-list" aria-label="Finished sub-agents">{finished.map((a) => row(a, false))}</ul>
          </details>
        )}
      </div>
    </section>
  );
}
