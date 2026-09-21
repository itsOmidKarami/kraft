import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ShortId } from "../../../components/ShortId";
import { clock, elapsed, elapsedBetween, statusWord } from "../../../format";
import type { KraftEvent, WorkerSession } from "../../../types";
import {
  detailOf,
  findingsOf,
  groupByNode,
  nodeRounds,
  taskRunLabel,
  titleOf,
  verdictWord,
  type Round,
  type TimelineSelection,
} from "../timelineHelpers";

/**
 * Right pane · Timeline (UI v2 · 05, 15; W13 · D): the event stream of what the
 * Timeline list selected -- a session, a round, one event -- or the whole
 * scoped node when nothing is. A session is one row, a run of plan_progress is
 * one row, a gap over two minutes is a `waiting` hairline; `all | gates |
 * tasks` narrows the rows as before.
 */

const GATE_TYPES = new Set(["gate_requested", "gate_approved", "gate_rejected", "chain_spliced"]);
const SESSION_TYPES = new Set(["worker_session_created", "worker_session_started", "worker_session_exited", "escalation_message"]);
const GAP_MS = 2 * 60_000;

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const sessionOf = (e: KraftEvent) => (typeof e.payload.session_id === "string" ? e.payload.session_id : null);

/** One session's times, from its events first and the store's row second. */
export interface SessionRun {
  id: string;
  hook: string;
  created: string;
  started: string | null;
  exited: string | null;
  status: string | null;
}

export type StreamRow =
  | { kind: "session"; at: string; end: string; run: SessionRun }
  | { kind: "tasks"; at: string; end: string; first: KraftEvent; last: KraftEvent }
  | { kind: "findings"; at: string; end: string; event: KraftEvent }
  | { kind: "judge"; at: string; end: string; event: KraftEvent }
  | { kind: "gate"; at: string; end: string; event: KraftEvent }
  | { kind: "event"; at: string; end: string; event: KraftEvent }
  | { kind: "gap"; at: string; end: string; ms: number };

/** D.2: the rows a list of events streams as, oldest first. */
export function streamRows(events: KraftEvent[], sessions: WorkerSession[] = []): StreamRow[] {
  const asc = [...events].sort((a, b) => a.seq - b.seq);
  const store = new Map(sessions.map((s) => [s.id, s]));
  const runs = new Map<string, SessionRun>();
  const rows: StreamRow[] = [];
  let tasks: Extract<StreamRow, { kind: "tasks" }> | null = null;
  for (const e of asc) {
    const sid = sessionOf(e);
    if (e.type !== "plan_progress") tasks = null;
    if (sid && SESSION_TYPES.has(e.type)) {
      let run = runs.get(sid);
      if (!run) {
        const s = store.get(sid);
        const hook = typeof e.payload.hook_point === "string" ? e.payload.hook_point : (s?.hook_point ?? "session");
        run = { id: sid, hook, created: s?.created_at ?? e.created_at, started: s?.started_at ?? null, exited: s?.exited_at ?? null, status: s?.status ?? null };
        runs.set(sid, run);
        rows.push({ kind: "session", at: run.created, end: run.created, run });
      }
      if (e.type === "worker_session_started") run.started = e.created_at;
      if (e.type === "worker_session_exited") {
        run.exited = e.created_at;
        if (typeof e.payload.status === "string") run.status = e.payload.status;
      }
      continue;
    }
    if (e.type === "plan_progress") {
      if (tasks) {
        tasks.last = e;
        tasks.end = e.created_at;
        continue;
      }
      tasks = { kind: "tasks", at: e.created_at, end: e.created_at, first: e, last: e };
      rows.push(tasks);
      continue;
    }
    const kind = e.type === "findings_measured" ? "findings" : e.type === "judge_verdict" ? "judge" : GATE_TYPES.has(e.type) ? "gate" : "event";
    rows.push({ kind, at: e.created_at, end: e.created_at, event: e });
  }
  for (const r of rows) if (r.kind === "session") r.end = r.run.exited ?? r.run.started ?? r.run.created;
  // A gap over two minutes between one row's end and the next row's start.
  const out: StreamRow[] = [];
  let prevEnd: string | null = null;
  for (const r of rows) {
    if (prevEnd) {
      const ms = Date.parse(r.at) - Date.parse(prevEnd);
      if (ms > GAP_MS) out.push({ kind: "gap", at: prevEnd, end: r.at, ms });
    }
    out.push(r);
    if (!prevEnd || r.end > prevEnd) prevEnd = r.end;
  }
  return out;
}

/** What the pane streams for a selection, and the header that names it (D.1, D.4). */
function scopeOf(
  sel: TimelineSelection | null,
  events: KraftEvent[],
  sessions: WorkerSession[],
  nodeId: string | null,
): { events: KraftEvent[]; head: ReactNode; session?: WorkerSession; sessionId?: string } {
  const nodeEvents = (node: string | null) => (groupByNode(events).find((g) => g.node === node)?.events ?? []).slice().reverse();
  const roundOfSession = (s: WorkerSession, rounds: Round[]) => rounds.find((r) => r.sessions.some((x) => x.id === s.id));
  const roundEvents = (own: KraftEvent[], r: Round) => {
    const ids = new Set(r.sessions.map((s) => s.id));
    return own.filter((e) => {
      const sid = sessionOf(e);
      if (sid) return ids.has(sid);
      return e.created_at >= r.startedAt && (!r.endedAt || e.created_at <= r.endedAt) && e.type !== "node_started";
    });
  };
  if (sel?.kind === "event") {
    const e = events.find((x) => x.seq === sel.seq);
    if (e) return { events: [e], head: `${e.type} · ${clock(e.created_at)}` };
  }
  if (sel?.kind === "session") {
    const s = sessions.find((x) => x.id === sel.id);
    const node = s?.node_id ?? nodeId;
    const own = nodeEvents(node);
    const from = s?.created_at ?? "";
    const to = s?.exited_at ?? null;
    const nr = node ? nodeRounds(node, events, sessions) : null;
    const rounds = nr?.rounds ?? [];
    const r = s ? roundOfSession(s, rounds) : undefined;
    // W14 · A: a session in a round streams its round, the session row lit, so
    // Up/Down has its neighbours; an escalation turn streams on its own.
    const mine =
      nr && r
        ? roundEvents(nr.events, r)
        : own.filter((e) => sessionOf(e) === sel.id || (e.type === "plan_progress" && e.created_at >= from && (!to || e.created_at <= to)));
    const bits = [
      s?.hook_point ?? "session",
      s?.hook_point === "escalation"
        ? `thread ${s.thread} · turn ${sessions.filter((x) => x.hook_point === "escalation" && x.thread === s.thread && x.created_at <= s.created_at).length}`
        : rounds.length > 1 && r
          ? `round ${r.n + 1}`
          : null,
      s ? statusWord(s.status) : null,
      s ? elapsedBetween(s.started_at ?? s.created_at, s.exited_at) : null,
    ].filter(Boolean);
    return { events: mine, head: bits.join(" · "), session: s, sessionId: sel.id };
  }
  if (sel?.kind === "round") {
    const nr = nodeRounds(sel.node, events, sessions);
    const r = nr.rounds[sel.n];
    if (r) {
      const mine = roundEvents(nr.events, r);
      const head = [
        `round ${r.n + 1}`,
        elapsedBetween(r.startedAt, r.endedAt),
        plural(r.sessions.length, "session"),
        r.findings.length ? plural(r.findings.length, "finding") : null,
      ]
        .filter(Boolean)
        .join(" · ");
      return { events: mine, head: r.verdict ? `${head} → ${verdictWord(r.verdict)}` : head };
    }
  }
  const node = sel?.kind === "node" ? sel.node : nodeId;
  const own = nodeEvents(node);
  const group = groupByNode(events).find((g) => g.node === node);
  return { events: own, head: `${node ?? "—"} · ${plural(own.length, "event")}${group ? ` · ${group.span}` : ""}` };
}

/** D.3: `HH:MM:SS` on the first row of a minute, `+Ns` from the row before otherwise. */
function stamps(rows: StreamRow[]): string[] {
  let lastMinute = "";
  let prev: string | null = null;
  return rows.map((r) => {
    if (r.kind === "gap") return "";
    const minute = clock(r.at).slice(0, 5);
    const out = minute !== lastMinute || !prev ? clock(r.at) : `+${elapsed(Date.parse(r.at) - Date.parse(prev))}`;
    lastMinute = minute;
    prev = r.at;
    return out;
  });
}

export function Events({
  events,
  sessions = [],
  nodeId,
  selection = null,
  onSelect,
  onViewLog,
}: {
  events: KraftEvent[];
  sessions?: WorkerSession[];
  nodeId: string | null;
  /** What the Timeline list picked (W13 · C.4); null streams the node. */
  selection?: TimelineSelection | null;
  /** W14 · A: a session row picked here, as `session:<id>`. */
  onSelect?: (id: string) => void;
  onViewLog: (sessionId: string) => void;
}) {
  const [filter, setFilter] = useState<"all" | "gates" | "tasks">("all");
  const listRef = useRef<HTMLOListElement>(null);
  const refocus = useRef(false);
  const selectedSession = selection?.kind === "session" ? selection.id : null;
  // Picking a session can restream the pane (node → its round); keep focus on the lit row.
  useEffect(() => {
    if (!refocus.current) return;
    refocus.current = false;
    listRef.current?.querySelector<HTMLElement>('[data-srow][data-selected="true"]')?.focus();
  }, [selectedSession]);

  // W14 · A: session rows are the pane's rows -- Up/Down move the selection, Enter opens the log.
  const onRowsKey = (e: KeyboardEvent<HTMLOListElement>) => {
    const rows = [...(listRef.current?.querySelectorAll<HTMLElement>("[data-srow]") ?? [])];
    const at = rows.indexOf(e.target as HTMLElement);
    if (at < 0) return;
    if (e.key === "Enter") {
      e.preventDefault();
      onViewLog(rows[at].dataset.srow!);
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = rows[Math.min(rows.length - 1, Math.max(0, at + (e.key === "ArrowDown" ? 1 : -1)))];
      if (next === rows[at]) return;
      refocus.current = true;
      next.focus();
      onSelect?.(`session:${next.dataset.srow}`);
    }
  };
  const hooks = new Map(sessions.map((s) => [s.id, s.hook_point]));
  const scope = scopeOf(selection, events, sessions, nodeId);
  const all = streamRows(scope.events, sessions);
  const rows = all.filter((r) =>
    filter === "all" ? true : filter === "gates" ? r.kind === "gate" : r.kind === "tasks" || r.kind === "session",
  );
  const times = stamps(rows);

  const body = (r: StreamRow): ReactNode => {
    switch (r.kind) {
      case "gap":
        return <span className="stream-gap-label">waiting {elapsed(r.ms)}</span>;
      case "session": {
        const { run } = r;
        const offset = (iso: string | null) => (iso ? `+${elapsed(Date.parse(iso) - Date.parse(run.created))}` : "—");
        return (
          <>
            <span className="stream-title">{run.hook}</span>
            <span className="stream-meta">
              created {clock(run.created)} · started {offset(run.started)} · exited {offset(run.exited)}
              {run.status && ` · ${statusWord(run.status)}`}
            </span>
            <button className="btn btn-ghost event-log" onClick={() => onViewLog(run.id)}>
              view log
            </button>
          </>
        );
      }
      case "tasks":
        return <span className="stream-title">{taskRunLabel(r.first, r.last)}</span>;
      case "findings": {
        const list = findingsOf(r.event);
        return (
          <>
            <span className="stream-title">{list.length ? plural(list.length, "finding") : (detailOf(r.event) ?? "no findings")}</span>
            {list.length > 0 && (
              <ul className="stream-findings">
                {list.map((f, i) => {
                  const where = f.file ? `${f.file}:${f.line ?? "?"}` : "—";
                  return (
                    <li key={`${f.source_plugin}:${f.file}:${i}`}>
                      <span
                        className="tag tag-neutral stream-severity"
                        title={
                          f.reported_severity
                            ? `this review rated it ${f.reported_severity}; held at ${f.severity} because the code it flagged had not changed`
                            : undefined
                        }
                      >
                        {f.severity || "—"}
                        {f.reported_severity ? ` (was rated ${f.reported_severity})` : ""}
                      </span>
                      <span className="doc-path stream-where" data-allow-ellipsis title={where}>
                        <span dir="ltr">{where}</span>
                      </span>
                      <span className="stream-message" title={f.message}>
                        {f.message}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </>
        );
      }
      case "judge": {
        const p = r.event.payload as Record<string, unknown>;
        return (
          <>
            <span className="stream-title">judge · {typeof p.verdict === "string" ? verdictWord(p.verdict) : "verdict"}</span>
            {typeof p.reasoning === "string" && p.reasoning && <q className="stream-quote">{p.reasoning}</q>}
          </>
        );
      }
      case "gate": {
        const p = r.event.payload as Record<string, unknown>;
        return (
          <>
            <span className="stream-title">
              {[r.event.type, typeof p.gate === "string" ? p.gate : null, typeof p.artifact === "string" ? "artifact" : null]
                .filter(Boolean)
                .join(" · ")}
            </span>
            {/* Its own line, so the left cut has the row's width to cut against. */}
            {typeof p.artifact === "string" && (
              <span className="doc-path stream-where stream-artifact" data-allow-ellipsis title={p.artifact}>
                <span dir="ltr">{p.artifact}</span>
              </span>
            )}
            {typeof p.note === "string" && <q className="stream-quote">{p.note}</q>}
          </>
        );
      }
      case "event": {
        const title = titleOf(r.event, hooks);
        const detail = detailOf(r.event);
        return (
          <>
            <span className="stream-title">{title ?? r.event.type}</span>
            {detail && <span className="stream-meta">{detail}</span>}
          </>
        );
      }
    }
  };

  return (
    <div className="pane events-pane" data-testid="right-pane-events">
      <header className="diff-modal-head">
        <span className="stream-head">{scope.head}</span>
        {scope.sessionId && (
          <>
            <button className="btn btn-ghost event-log" onClick={() => onViewLog(scope.sessionId!)}>
              view log
            </button>
            <ShortId id={scope.sessionId} />
          </>
        )}
        <div className="log-filters">
          <button className="log-chip" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
            all
          </button>
          <button className="log-chip" aria-pressed={filter === "gates"} onClick={() => setFilter("gates")}>
            gates
          </button>
          <button className="log-chip" aria-pressed={filter === "tasks"} onClick={() => setFilter("tasks")}>
            tasks
          </button>
        </div>
      </header>
      {rows.length === 0 && <p className="empty">no {filter === "gates" ? "gate " : ""}events here</p>}
      <ol className="stream" ref={listRef} onKeyDown={onRowsKey}>
        {rows.map((r, i) => (
          <li
            key={`${r.kind}:${r.at}:${i}`}
            className="stream-row"
            data-kind={r.kind}
            data-type={"event" in r ? r.event.type : r.kind === "session" ? "worker_session" : r.kind === "tasks" ? "plan_progress" : undefined}
            {...(r.kind === "session" && {
              "data-srow": r.run.id,
              "data-selected": r.run.id === selectedSession,
              "aria-current": r.run.id === selectedSession ? ("true" as const) : undefined,
              tabIndex: 0,
              onClick: () => onSelect?.(`session:${r.run.id}`),
            })}
          >
            <div className="stream-body">{body(r)}</div>
            {r.kind !== "gap" && <time className="stream-time">{times[i]}</time>}
          </li>
        ))}
      </ol>
    </div>
  );
}
