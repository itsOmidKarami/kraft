import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { agentsIn, clock, inAgent, lineCount, logLineText } from "../../../format";
import type { LogLine } from "../../../types";
import { useModal } from "../../../useModal";
import { Maximize2 } from "../../icons";
import { logTextUrl } from "../../../api";
import { POLL_MS, useLogLines } from "../useLogLines";
import { LinkButton } from "../../ui/LinkButton";
import { Menu, type MenuItem } from "../../ui/Menu";

const SOURCES = ["sys", "stdout", "agent", "tool"] as const;
export { POLL_MS };

/** What the log says when it is filtered to a sub-agent none of the loaded lines name. */
export const AGENT_LINES_EARLIER = "This sub-agent's lines are before the part shown.";

/** A session's log (GAP §2 #4, #40): source chips, follow, copy, the whole
 *  file when the view was cut, full screen. Read again every few seconds
 *  while the session runs. */
export function Log({ workItemId, sessionId, running, title, crumb, agent, onAgent }: { workItemId: string; sessionId: string; running: boolean; title: string; crumb?: string; agent?: string; onAgent?: (agent: string | undefined) => void }) {
  const { lines, error } = useLogLines(workItemId, sessionId, running);
  const [off, setOff] = useState<Set<string>>(new Set());
  const [follow, setFollow] = useState(true);
  const [full, setFull] = useState(false);

  const cut = lines?.find((l) => l.truncated);
  const agents = agentsIn(lines ?? []);
  const shown = (lines ?? []).filter((l) => !l.truncated && !off.has(l.src) && inAgent(l, agent));
  const missing = !!agent && agent !== "main" && !!lines && !agents.some((a) => a.id === agent);
  const body = (big: boolean) => <LogBody lines={shown} follow={follow} onFollow={setFollow} big={big} />;
  const toggle = (s: string) => setOff((o) => { const n = new Set(o); if (n.has(s)) n.delete(s); else n.add(s); return n; });
  const controls = (
    <>
      {(agents.length > 0 || agent) && <AgentMenu agents={agents} agent={agent} onAgent={onAgent} />}
      <span className="ip-chips" role="group" aria-label="Sources">
        {SOURCES.map((s) => <button key={s} type="button" className="ip-chip" aria-pressed={!off.has(s)} onClick={() => toggle(s)}>{s}</button>)}
      </span>
      <span className="ip-follow-copy">
        <label className="item-check ip-follow"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow</label>
        <LinkButton onClick={() => navigator.clipboard?.writeText(shown.map(logLineText).join("\n"))}>copy</LinkButton>
      </span>
    </>
  );
  const count = lines ? lineCount(shown.length) : "Reading…";
  if (error) return <p className="item-muted">{error}</p>;
  return (
    <div className="ip-log-wrap">
      <div className="ip-log-head">
        <span className="item-muted">{count}</span>
        {controls}
        <LinkButton className="ip-full" onClick={() => setFull(true)}><Maximize2 size={12} aria-hidden /> full screen</LinkButton>
      </div>
      {cut?.truncated && (
        <p className="item-muted">
          {cut.truncated.lines} earlier lines are not shown. <LinkButton href={logTextUrl(workItemId, sessionId)} download={`${workItemId}-${sessionId}.log`}>Download the whole log</LinkButton>
        </p>
      )}
      {missing && (
        <p className="item-muted">
          {AGENT_LINES_EARLIER} <LinkButton href={logTextUrl(workItemId, sessionId)} download={`${workItemId}-${sessionId}.log`}>Download the whole log</LinkButton>
        </p>
      )}
      {body(false)}
      {full && (
        <LogScreen title={title} crumb={crumb} count={lines ? `log · ${lineCount(shown.length)}` : "log · reading…"} controls={controls} onClose={() => setFull(false)}>
          {body(true)}
        </LogScreen>
      )}
    </div>
  );
}

/** Which sub-agent's lines the log shows, as a pill and a menu in the pattern of the attempt menu: All, Main, then
 *  each sub-agent with a status dot and its type. A menu, not chips, because it holds many long descriptions. */
function AgentMenu({ agents, agent, onAgent }: { agents: ReturnType<typeof agentsIn>; agent?: string; onAgent?: (agent: string | undefined) => void }) {
  // `Menu` keys its rows by label: two sub-agents given the same description are told apart by id.
  const twin = (label: string) => agents.filter((a) => a.label === label).length > 1;
  const named = agents.map((a) => ({ ...a, name: twin(a.label) ? `${a.label} (${a.id})` : a.label }));
  const pick = (id: string | undefined): MenuItem["onSelect"] => () => onAgent?.(id);
  const items: MenuItem[] = [
    { label: "All", checked: !agent, onSelect: pick(undefined) },
    { label: "Main", checked: agent === "main", onSelect: pick("main") },
    ...named.map((a): MenuItem => ({
      label: a.name,
      hint: a.type,
      icon: <span className={`ip-pill-dot ${a.status === "running" ? "is-live" : a.status === "completed" ? "is-ok" : "is-bad"}`} />,
      checked: a.id === agent,
      onSelect: pick(a.id),
    })),
  ];
  const now = !agent ? "All" : agent === "main" ? "Main" : named.find((a) => a.id === agent)?.name ?? agent;
  return <Menu label="Agents" triggerClass="ip-pill" trigger={<>agent: {now}<span className="ip-pill-caret" aria-hidden>▾</span></>} items={items} />;
}

/** The log over the whole viewport, not a dialog box: Escape or the exit button
 *  leaves it, and focus goes back to the full screen button. */
function LogScreen({ title, crumb, count, controls, onClose, children }: { title: string; crumb?: string; count: string; controls: ReactNode; onClose: () => void; children: ReactNode }) {
  const ref = useModal<HTMLDivElement>(onClose);
  return createPortal(
    <div ref={ref} role="dialog" aria-modal="true" aria-label={`${title} · log`} tabIndex={-1} className="ip-log-screen">
      <div className="ip-log-head ip-log-screen-head">
        <span className="ip-log-crumb">{crumb && <span className="item-muted">{crumb} › </span>}<span className="ip-log-title">{title}</span></span>
        <span className="item-muted">{count}</span>
        {controls}
        <LinkButton className="ip-full" onClick={onClose}>⤡ exit full screen</LinkButton>
      </div>
      {children}
    </div>,
    document.body,
  );
}

/** The <pre> is the scroller, in the pane as in full screen. Following keeps it at the
 *  newest line; scrolling up stops following, scrolling back to the bottom resumes it. */
function LogBody({ lines, follow, onFollow, big }: { lines: LogLine[]; follow: boolean; onFollow: (on: boolean) => void; big: boolean }) {
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (follow && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [lines, follow]);
  const onScroll = () => {
    const el = ref.current!;
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 4;
    if (bottom !== follow) onFollow(bottom);
  };
  return (
    <pre ref={ref} className={`ip-log${big ? " is-big" : ""}`} tabIndex={0} aria-label="Log lines" onScroll={onScroll}>
      {lines.map((l) => (
        <span key={l.n} className={`ip-log-line src-${l.src}`}>
          <span className="ip-log-t">{l.t ? (l.t.includes("T") ? clock(l.t) : l.t) : ""}</span>
          {logLineText(l)}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}
