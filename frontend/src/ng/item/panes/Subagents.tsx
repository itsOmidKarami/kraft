import type { Subagent } from "../../../types";
import { countLine, PROMPT_NOT_SHOWN, statusTone, subagentMeta, subagentSaid } from "../subagents";
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

/** A task's sub-agents (Overview): a count line, then a row each. The row opens the task's log filtered to it. */
export function SubagentSection({ workItemId, sessionId, list, onOpen }: { workItemId: string; sessionId: string; list: Subagent[] | null; onOpen: (agent: string) => void }) {
  if (!list?.length) return null;
  return (
    <>
      <h3 className="ip-h">Sub-agents</h3>
      <p className="item-muted ip-subagent-count">{countLine(list)}</p>
      <ul className="ip-list" aria-label="Sub-agents">
        {list.map((a) => {
          const said = subagentSaid(a);
          return (
            <li key={a.id} className="ip-subagent">
              <button type="button" className="ip-row" onClick={() => onOpen(a.id)} aria-label={`${a.description} — open its log`}>
                <span className={`ip-pill-dot ${statusTone(a.status)}`} aria-hidden />
                <span className="ip-subagent-name">{a.description}</span>
                <span className="ip-row-meta">{subagentMeta(a)}</span>
              </button>
              {said && <p className="item-muted ip-subagent-said">{said}</p>}
              <Prompt workItemId={workItemId} sessionId={sessionId} agent={a.id} />
            </li>
          );
        })}
      </ul>
    </>
  );
}
