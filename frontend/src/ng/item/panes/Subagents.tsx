import { useState } from "react";
import { subagentUrl } from "../../../api";
import { elapsed, tokens } from "../../../format";
import type { Subagent, SubagentDetail } from "../../../types";
import { request } from "../../http";
import { subagentCount } from "../useSubagents";

/** What the Overview says of a prompt the part of the log read does not hold. */
export const PROMPT_NOT_SHOWN = "This sub-agent's prompt is before the part of the log shown.";

/** The words for a sub-agent's status. */
export const STATUS_WORDS: Record<Subagent["status"], string> = { running: "running", completed: "done", failed: "failed", killed: "stopped", cut_off: "cut off" };

/** "2 running · 3 finished · 1 cut off", leaving out what is zero. */
export function countLine(list: Subagent[]): string {
  const c = subagentCount(list);
  return [c.running && `${c.running} running`, c.finished && `${c.finished} finished`, c.cutOff && `${c.cutOff} cut off`].filter(Boolean).join(" · ");
}

/** The dot a sub-agent's status draws (the pill dots of the attempt menu). */
export const statusTone = (status: Subagent["status"]) => (status === "running" ? "is-live" : status === "completed" ? "is-ok" : "is-bad");

const meta = (a: Subagent) => [
  a.type,
  STATUS_WORDS[a.status],
  a.tokens != null ? `${tokens(a.tokens)} tokens` : null,
  a.tool_uses != null ? `${a.tool_uses} tool use${a.tool_uses === 1 ? "" : "s"}` : null,
  a.duration_ms != null ? elapsed(a.duration_ms) : null,
].filter(Boolean).join(" · ");

/** The prompt behind a disclosure, fetched whole when it is opened: the list carries none. */
function Prompt({ workItemId, sessionId, agent }: { workItemId: string; sessionId: string; agent: string }) {
  const [detail, setDetail] = useState<SubagentDetail | "failed" | null>(null);
  const load = () => {
    if (detail) return;
    void request<SubagentDetail>(subagentUrl(workItemId, sessionId, agent)).then((r) => setDetail(r.status === 200 ? r.body : "failed"));
  };
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

/** A task's sub-agents (Overview, web and phone): a count line, then a row each. The row opens the task's log filtered to it. */
export function SubagentSection({ workItemId, sessionId, list, onOpen }: { workItemId: string; sessionId: string; list: Subagent[] | null; onOpen: (agent: string) => void }) {
  if (!list?.length) return null;
  return (
    <>
      <h3 className="ip-h">Sub-agents</h3>
      <p className="item-muted ip-subagent-count">{countLine(list)}</p>
      <ul className="ip-list" aria-label="Sub-agents">
        {list.map((a) => {
          const said = a.status === "running" ? (a.last_tool ? `running ${a.last_tool}` : "") : (a.summary ?? "").split("\n")[0];
          return (
            <li key={a.id} className="ip-subagent">
              <button type="button" className="ip-row" onClick={() => onOpen(a.id)} aria-label={`${a.description} — open its log`}>
                <span className={`ip-pill-dot ${statusTone(a.status)}`} aria-hidden />
                <span className="ip-subagent-name">{a.description}</span>
                <span className="ip-row-meta">{meta(a)}</span>
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
