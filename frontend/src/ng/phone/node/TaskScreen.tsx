import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as api from "../../../api";
import { ago, elapsed, tokens, usd } from "../../../format";
import type { KraftEvent, WorkerSession } from "../../../types";
import { escalationsOf, ESCALATION, lookWord, messagesThrough, sessionLook, sessionsOf } from "../../item/nodeGraph";
import { stepsOf } from "../../item/paths";
import { selPath, type Place } from "../../item/url";
import { LogLines } from "../log/LogLines";
import { useLog } from "../log/useLog";
import { ScreenHeader } from "../nav/ScreenHeader";
import { Block, Facts, TabStrip } from "../ui/Rows";
import type { PlaceProps } from "./NodeScreen";
import "./node.css";

type TaskTab = "overview" | "log" | "config" | "thread";
const SOURCES = ["all", "agent", "tool", "sys", "stdout"] as const;

/** A task of a node, or the node's escalation (W17 brief E): its attempts, and Overview / Log / Config, with Thread first on an escalation. */
export function TaskScreen({ item, version, docs, place, node: nodeId, now, setPlace }: PlaceProps & { place: Place & { sel: { kind: "task"; node: string; step: string; task: string } } }) {
  const navigate = useNavigate();
  const path = selPath(place.sel)!;
  const esc = place.sel.step === ESCALATION;
  const sessions = esc ? escalationsOf(item, nodeId) : sessionsOf(item, path);
  // An escalation's picker is per thread: it shows that thread through its last turn.
  const wanted = place.attempt ? (esc ? sessions.map((s) => s.thread).lastIndexOf(place.attempt) : sessions.findIndex((s) => s.attempt === place.attempt)) : -1;
  const at: WorkerSession | undefined = sessions[wanted >= 0 ? wanted : sessions.length - 1];
  const look = sessionLook(at, now);
  const tabs: { id: TaskTab; label: string }[] = [...(esc ? [{ id: "thread" as const, label: "Thread" }] : []), { id: "overview", label: "Overview" }, { id: "log", label: "Log" }, { id: "config", label: "Config" }];
  const tab = (tabs.some((t) => t.id === place.tab) ? place.tab : tabs[0].id) as TaskTab;
  const name = esc ? "escalation" : place.sel.task;
  const mine = docs.filter((d) => at && (d.worker_session_id === at.id || (d.hook_point === path && d.attempt === at.attempt)));
  const state = at ? `${esc ? "handler · " : ""}${at.model ? "agent task" : "task"} · ${lookWord(look)}${sessions.length > 1 ? ` · attempt ${sessions.indexOf(at) + 1} of ${sessions.length}` : ""}` : "task · not started";
  const apiNode = item.chain_definition.nodes.find((n) => n.id === nodeId)!;
  const steps = stepsOf(apiNode).steps;
  const si = steps.findIndex((s) => s.id === place.sel.step);
  const before = si > 0 ? `step ${steps[si - 1].id}` : (() => { const i = item.chain_definition.nodes.findIndex((n) => n.id === nodeId); return i > 0 ? item.chain_definition.nodes[i - 1].id : null; })();
  const judged = (item.judge_stop_note ?? []).filter((j) => j.node_id === nodeId);
  const summary = at?.session_summary_ref ? docs.find((d) => d.path === at.session_summary_ref) : undefined;

  return (
    <>
      <ScreenHeader />
      <div className="ph-content">
        <div className="ph-node-head">
          <p className="ph-crumb">{item.bead_id ?? item.id.slice(0, 8)} › {nodeId}{esc ? "" : ` › ${place.sel.step}`}</p>
          <h1 className="ph-node-title">{name}</h1>
          <p className={`ph-node-sub${look.state === "failed" ? " ph-tone-bad" : look.running ? " ph-tone-info" : ""}`}>{state}</p>
        </div>
        <TabStrip label="Task" tabs={tabs} value={tab} onChange={(t) => setPlace({ tab: t === tabs[0].id ? undefined : t })} />
        {sessions.length > 1 && (
          <div className="ph-attempts" role="group" aria-label="Attempts">
            {sessions.map((s) => (
              <button key={s.id} type="button" className={`ph-attempt${s === at ? " ph-is-on" : ""}`} aria-pressed={s === at} onClick={() => setPlace({ attempt: s === sessions.at(-1) ? undefined : esc ? s.thread : s.attempt })}>
                {esc ? `thread ${s.thread}` : `#${s.attempt}`} · {sessionLook(s, now).running ? "running" : lookWord(sessionLook(s, now))}
              </button>
            ))}
          </div>
        )}
        {tab === "overview" && (
          at ? (
            <>
              <Facts rows={[
                ["state", at.status.replaceAll("_", " ")],
                ...(at.harness ? ([["harness", <span key="h" className="ph-mono">{at.harness}</span>]] as [string, React.ReactNode][]) : []),
                ...(at.model ? ([["model", <span key="m" className="ph-mono">{at.model}</span>]] as [string, React.ReactNode][]) : []),
                ["path", <span key="p" className="ph-mono">{path}</span>],
                ...(at.wall_ms != null ? ([["duration", elapsed(at.wall_ms)]] as [string, React.ReactNode][]) : []),
                ...(at.round ? ([["round", `fix loop round ${at.round + 1}`]] as [string, React.ReactNode][]) : []),
              ]} />
              <Block title="Result">
                <Facts rows={[
                  ...(at.tokens_in != null ? ([["tokens", tokens((at.tokens_in ?? 0) + (at.tokens_out ?? 0))]] as [string, React.ReactNode][]) : []),
                  ...(at.cost_usd != null ? ([["cost", usd(at.cost_usd, true, at.cost_estimated)]] as [string, React.ReactNode][]) : []),
                  ...(summary ? ([["summary", <button key="s" type="button" className="ph-linkbtn" onClick={() => navigate(`?doc=${encodeURIComponent(summary.document_id)}`)}>{summary.title}</button>]] as [string, React.ReactNode][]) : []),
                ]} />
              </Block>
              {judged.length > 0 && judged.map((j, i) => (
                <Block key={i} title="Findings the judge stopped chasing">
                  <p className="ph-note">{j.reasoning}</p>
                  <ul className="ph-findings">{j.findings.map((f, k) => <li key={k}><span className="ph-mono">{f.severity}</span> {f.file ? `${f.file}${f.line ? `:${f.line}` : ""} · ` : ""}{f.message}</li>)}</ul>
                </Block>
              ))}
              <Block title="Documents">
                {mine.length ? (
                  <div className="ph-list">
                    {mine.map((d) => (
                      <button key={d.document_id} type="button" className="ph-row" onClick={() => navigate(`?doc=${encodeURIComponent(d.document_id)}`)}>
                        <span className="ph-row-text"><span className="ph-row-label">{d.title}</span><span className="ph-row-hint ph-mono">{d.path}</span></span>
                      </button>
                    ))}
                  </div>
                ) : <p className="ph-note">This attempt wrote no documents.</p>}
              </Block>
            </>
          ) : (
            <Block title="When it runs">
              <p className="ph-note">{before ? `After ${before} finishes.` : "When the item starts."} It has no attempt yet.</p>
            </Block>
          )
        )}
        {tab === "log" && <TaskLog session={at} />}
        {tab === "config" && (
          <Facts rows={[
            ["path", <span key="p" className="ph-mono">{path}</span>],
            ...(at?.model ? ([["model", <span key="m" className="ph-mono">{at.model}</span>]] as [string, React.ReactNode][]) : []),
            ...(at ? ([["attempt", String(at.attempt)]] as [string, React.ReactNode][]) : []),
            ...(at?.head_sha ? ([["head", <span key="h" className="ph-mono">{at.head_sha.slice(0, 10)}</span>]] as [string, React.ReactNode][]) : []),
          ]} />
        )}
        {tab === "thread" && <Thread item={item} version={version} node={nodeId} upTo={at === sessions.at(-1) ? undefined : at} />}
      </div>
    </>
  );
}

function TaskLog({ session }: { session?: WorkerSession }) {
  const [src, setSrc] = useState<(typeof SOURCES)[number]>("all");
  const running = session?.status === "running" || session?.status === "pending";
  const { lines, error } = useLog(session?.id ?? null, !!running);
  const shown = (lines ?? []).filter((l) => src === "all" || l.src === src);
  return (
    <>
      <div className="ph-chips ph-chips-inline" role="group" aria-label="Sources">
        {SOURCES.map((s) => <button key={s} type="button" className={`ph-chip${src === s ? " ph-is-on" : ""}`} aria-pressed={src === s} onClick={() => setSrc(s)}>{s}</button>)}
        <span className="ph-spacer" />
        <span className="ph-count">{lines ? `${lines.length} lines${running ? " · following" : ""}` : session ? "Reading…" : "0 lines"}</span>
      </div>
      {error ? <p className="ph-note">{error}</p> : <LogLines lines={shown} empty="No lines to show. Clear the filter, or the task has not started." />}
    </>
  );
}

/** The node's escalation thread: every message through the picked thread's last turn, by thread and turn. */
function Thread({ item, version, node, upTo }: { item: PlaceProps["item"]; version: string; node: string; upTo?: WorkerSession }) {
  const [events, setEvents] = useState<KraftEvent[] | null>(null);
  useEffect(() => {
    let live = true;
    api.getEvents(item.id).then((e) => live && setEvents(e), () => live && setEvents([]));
    return () => { live = false; };
  }, [item.id, version]);
  const all = (events ?? []).filter((e) => e.type === "escalation_message" && (e.node_id ?? node) === node).map((e) => ({ thread: Number(e.payload.thread ?? 1), turn: Number(e.payload.turn ?? 1), who: e.payload.auto ? "kraft" : "you", text: String(e.payload.message ?? ""), at: e.created_at, node, session: typeof e.payload.session_id === "string" ? e.payload.session_id : null }));
  const turns = all.slice(0, messagesThrough(all, upTo, item));
  if (events == null) return <p className="ph-note">Reading the thread…</p>;
  if (!turns.length) return <p className="ph-note">No messages yet.</p>;
  return (
    <>
    {turns.length < all.length && <p className="ph-note">{all.length - turns.length} later {all.length - turns.length === 1 ? "message" : "messages"} after this thread.</p>}
    <ol className="ph-turns">
      {turns.map((t, i) => (
        <li key={i} className="ph-turn">
          <p className="ph-turn-head"><span className="ph-mono">thread {t.thread} · turn {t.turn}</span><span>{ago(t.at)}</span></p>
          <p className="ph-turn-who ph-mono">{t.who}</p>
          <p className="ph-turn-text">{t.text}</p>
        </li>
      ))}
    </ol>
    </>
  );
}
