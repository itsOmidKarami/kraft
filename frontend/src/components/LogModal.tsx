import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLineDown, Copy, X } from "@phosphor-icons/react";
import * as api from "../api";
import { clock, elapsed, tokens, usd } from "../format";
import { findSession } from "../store";
import type { LogLine } from "../types";
import { useModal } from "../useModal";
import { StatusGlyph } from "./ui";

/**
 * A worker session's log, over the work item (design 6c). Opened from a task
 * row, a `worker_session_*` timeline event, or a capped cycle — all of which
 * have only the session id, so the session's own metadata is looked up rather
 * than passed down.
 */

const CHIPS: { id: string; label: string }[] = [
  { id: "all", label: "all" },
  { id: "stdout", label: "stdout" },
  { id: "agent", label: "agent" },
  { id: "tool", label: "tool" },
  { id: "sys", label: "sys" },
];

/** Union by line number, kept in order — either source may arrive first. */
function merge(prev: LogLine[], incoming: LogLine[]): LogLine[] {
  const byLine = new Map(prev.map((l) => [l.n, l]));
  let changed = false;
  for (const line of incoming) {
    if (!byLine.has(line.n)) {
      byLine.set(line.n, line);
      changed = true;
    }
  }
  return changed ? [...byLine.values()].sort((a, b) => a.n - b.n) : prev;
}

export function LogModal({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [note, setNote] = useState<string | null>(null);
  const ref = useModal<HTMLDivElement>(onClose);
  const bodyRef = useRef<HTMLDivElement>(null);

  const session = findSession(sessionId);
  // a session still in 'pending' is about to write: follow it, do not wait
  const live = session?.status === "running" || session?.status === "pending";
  // Follow is on while the session is live, and the user can switch it off.
  const [follow, setFollow] = useState(live);
  // The stream is down but the browser is retrying it by itself.
  const [reconnecting, setReconnecting] = useState(false);

  useEffect(() => {
    // an error belongs to the session and follow-state it came from: without
    // this, one session's failure stays on screen over the next one's lines
    setError(null);
    // While following, the server's tail (`_tail`, src/kraft/api.py:1350)
    // starts at line 0 and sends everything -- a snapshot alongside it can
    // only duplicate the stream or fail. Switching follow off fetches it then,
    // which also picks up whatever the stream missed.
    if (follow) return;
    let alive = true;
    api
      .getLogLines(sessionId)
      // merge, never replace: the tail may already have delivered lines past the
      // end of this snapshot, and it never re-sends what it has sent
      .then((r) => alive && setLines((prev) => merge(prev, r.lines)))
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [sessionId, follow]);

  useEffect(() => {
    if (!follow || typeof EventSource === "undefined") return;
    setReconnecting(false);
    const es = new EventSource(api.logStreamUrl(sessionId));
    es.onmessage = (e) => {
      const line = JSON.parse(e.data) as LogLine;
      setReconnecting(false);
      // the tail replays from the top, so merge on the line number
      setLines((prev) => merge(prev, [line]));
    };
    es.addEventListener("end", () => {
      es.close();
      // the session is over: stop the "following" strip claiming otherwise,
      // and let the first effect pull a final snapshot
      setFollow(false);
    });
    es.onerror = () => {
      // EventSource reconnects itself; only CLOSED means it has given up.
      // Closing on both discards the browser's own retry. A reconnect replays
      // from line 0 and merge() drops the duplicates.
      if (es.readyState === EventSource.CLOSED) {
        setFollow(false);
        setNote("log stream stopped — press Follow to retry");
      } else {
        setReconnecting(true);
      }
    };
    return () => es.close();
  }, [sessionId, follow]);

  const shown = useMemo(
    () => (filter === "all" ? lines : lines.filter((l) => l.src === filter)),
    [lines, filter],
  );

  useEffect(() => {
    if (follow && bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [shown, follow]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(lines.map((l) => l.text).join("\n"));
      setNote("log copied");
    } catch {
      setNote("could not copy the log");
    }
  };

  const meta: string[] = [];
  if (session) {
    const span =
      session.wall_ms != null
        ? elapsed(session.wall_ms)
        : session.started_at
          ? elapsed(Date.now() - Date.parse(session.started_at))
          : null;
    meta.push(span ? `${session.status} · ${span}` : session.status);
    meta.push(session.round > 0 ? `${session.node_id} · cycle ${session.round}` : session.node_id);
    if (session.tokens_in != null) {
      const total = (session.tokens_in ?? 0) + (session.tokens_out ?? 0);
      meta.push(
        session.cost_usd != null
          ? `${tokens(total)} tokens · ${usd(session.cost_usd)}`
          : `${tokens(total)} tokens`,
      );
    }
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="session log">
      <div className="dialog log-modal elev-lg" ref={ref}>
        <header className="log-head">
          <StatusGlyph status={session?.status ?? "unknown"} />
          <div className="log-title">
            <span className="log-hook">{session?.hook_point ?? sessionId}</span>
            <div className="log-meta">
              {meta.map((m) => (
                <span key={m}>{m}</span>
              ))}
              <span className="log-sid">{sessionId}</span>
            </div>
          </div>
          <div className="log-actions">
            <button
              className="btn btn-secondary log-follow"
              aria-pressed={follow}
              onClick={() => setFollow((v) => !v)}
            >
              <ArrowLineDown size={13} />
              {follow ? "Following" : "Follow"}
            </button>
            <button className="btn btn-icon btn-ghost" title="Copy log" onClick={copy}>
              <Copy size={14} />
            </button>
            <button className="btn btn-icon btn-ghost" title="Close · Esc" onClick={onClose}>
              <X size={14} />
            </button>
          </div>
        </header>

        <div className="log-filters">
          {CHIPS.map((c) => (
            <button
              key={c.id}
              className="log-chip"
              aria-pressed={filter === c.id}
              onClick={() => setFilter(c.id)}
            >
              {c.label}
            </button>
          ))}
          <span className="log-count">{shown.length} lines</span>
        </div>

        <div className="log-body" ref={bodyRef}>
          {error && <p className="form-error">{error}</p>}
          {shown.length === 0 && !error && live && (
            <p className="empty">
              no output yet — an agent running with <code>--output-format json</code> writes
              its log when it exits
            </p>
          )}
          {shown.map((l) => (
            <div key={l.n} className="log-line" data-src={l.src}>
              <span className="log-t">{l.t ? clock(l.t) : ""}</span>
              <span className="log-src">{l.src}</span>
              <span className="log-text">{l.text}</span>
            </div>
          ))}
          {follow && (
            <div className="log-following">
              <span className="live-dot" />
              {reconnecting ? "reconnecting…" : "following · new lines appear here"}
            </div>
          )}
        </div>
        {note && <p className="doc-modal-note">{note}</p>}
      </div>
    </div>
  );
}
