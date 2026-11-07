import { useEffect } from "react";
import { elapsed, tokens } from "../../../format";
import type { Subagent, WorkerSession } from "../../../types";
import { PROMPT_NOT_SHOWN, STATUS_WORDS } from "../subagents";
import { useSubagentDetail } from "../useSubagents";
import { fact } from "./TaskPane";
import { Log } from "./Log";
import type { PaneContent } from "./paneContent";

type Args = { workItemId: string; s: WorkerSession; a: Subagent; task: string; crumbs: PaneContent["crumbs"]; tab: string; setAgent: (agent: string | undefined) => void; running: boolean };

/** What it did and was asked: its facts, what it last did (running) or its answer, and its whole prompt, read on open. */
function SubagentOverview({ workItemId, s, a }: { workItemId: string; s: WorkerSession; a: Subagent }) {
  const { detail, load } = useSubagentDetail(workItemId, s.id, a.id);
  useEffect(load, [workItemId, s.id, a.id]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <dl className="item-facts ip-facts">
        {fact("status", STATUS_WORDS[a.status])}
        {fact("type", a.type && <span className="is-mono">{a.type}</span>)}
        {fact("now", a.status === "running" && a.last_tool ? <>running <span className="is-mono">{a.last_tool}</span></> : null)}
        {fact("tokens", a.tokens != null ? tokens(a.tokens) : null)}
        {fact("tool uses", a.tool_uses != null ? String(a.tool_uses) : null)}
        {fact("ran", a.duration_ms != null ? elapsed(a.duration_ms) : null)}
      </dl>
      {a.status !== "running" && a.summary && (
        <>
          <h3 className="ip-h">Answer</h3>
          <p className="ip-subagent-answer">{a.summary}</p>
        </>
      )}
      <h3 className="ip-h">Prompt</h3>
      {detail === null ? <p className="item-muted">Reading…</p>
        : detail === "failed" ? <p className="item-muted">Could not read this sub-agent's prompt.</p>
        : detail.prompt ? <pre className="ip-prompt">{detail.prompt}</pre>
        : <p className="item-muted">{PROMPT_NOT_SHOWN}</p>}
    </>
  );
}

/** One of a task's sub-agents, a level under the task, as a changed-test-scope task's scope is: Overview and its own
 *  lines of the task's log. The task's crumb, Esc and the log's "All" step back to the task. */
export function subagentPane({ workItemId, s, a, task, crumbs, tab, setAgent, running }: Args): PaneContent {
  const on = tab === "log" ? "log" : "overview";
  const above = [...crumbs, { label: task, onClick: () => setAgent(undefined) }];
  return {
    crumbs: above,
    icon: "bot",
    title: a.description,
    sub: ["sub-agent", a.type, STATUS_WORDS[a.status], a.duration_ms != null ? elapsed(a.duration_ms) : null].filter(Boolean).join(" · "),
    tabs: [{ value: "overview", label: "Overview" }, { value: "log", label: "Log" }],
    body: on === "log"
      ? <Log key={s.id} workItemId={workItemId} sessionId={s.id} running={running} title={a.description} crumb={above.map((c) => c.label).join(" › ")} agent={a.id} onAgent={setAgent} />
      // Keyed by the sub-agent: another one picked reads its own prompt, not the last one's.
      : <SubagentOverview key={a.id} workItemId={workItemId} s={s} a={a} />,
  };
}
