import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLineDown, ArrowsOutSimple, Copy, DownloadSimple } from "@phosphor-icons/react";
import * as api from "../../../api";
import { clock, elapsed, logLineText, tokenTotal, tokens, usd } from "../../../format";
import { findSession, useStore } from "../../../store";
import type { LogLine } from "../../../types";
import { StatusGlyph } from "../../../components/ui";
import { ShortId } from "../../../components/ShortId";

/**
 * Right pane · Log (UI v2 · 05, 12/16): `LogModal`'s fetch/follow logic,
 * without the dialog framing — this is a pane now, not a modal (05's "no
 * modal opens from the item page for logs/diffs/documents").
 *
 * Kraft-3oau: a fetch error must not survive a reconnect. `store.connection`
 * flipping back to `"open"` re-runs the snapshot fetch (dropped from its
 * dependency list before this fix) and clears a stale error even while
 * following, where the effect below would otherwise never fire again.
 */

const CHIPS: { id: string; label: string }[] = [
  { id: "all", label: "all" },
  { id: "stdout", label: "stdout" },
  { id: "agent", label: "agent" },
  { id: "tool", label: "tool" },
  { id: "sys", label: "sys" },
];

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

export function Log({
  sessionId,
  maximized,
  onToggleMaximize,
  /** Mobile m04: the current-node log caps at 8 lines with a "Show all"
   *  below it, instead of the full scrolling pane desktop gets. */
  capLines,
}: {
  sessionId: string;
  maximized?: boolean;
  onToggleMaximize?: () => void;
  capLines?: number;
}) {
  const [expanded, setExpanded] = useState(!capLines);
  const [lines, setLines] = useState<LogLine[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [note, setNote] = useState<string | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const connection = useStore((s) => s.connection);

  const session = findSession(sessionId);
  const live = session?.status === "running" || session?.status === "pending";
  const [follow, setFollow] = useState(live);
  const [reconnecting, setReconnecting] = useState(false);

  useEffect(() => {
    setLines([]);
    setError(null);
    // RightPane renders <Log> without a `key`, so switching sessions (e.g.
    // finished → running in the Tasks list) reuses this instance: reset the
    // per-session UI state here instead of only seeding it from `live` once
    // at mount, or a running session opened after a finished one never starts
    // following.
    setFollow(live);
    setExpanded(!capLines);
    setNote(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    setError(null);
    if (follow) return;
    let alive = true;
    api
      .getLogLines(sessionId)
      .then((r) => alive && setLines((prev) => merge(prev, r.lines)))
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
    // `connection` is in the dependency list deliberately (Kraft-3oau): a
    // snapshot fetch that failed while the socket was down retries, and
    // clears its own stale error, the moment `connection` reports `"open"`
    // again — not just on the next unrelated re-render.
  }, [sessionId, follow, connection]);

  useEffect(() => {
    if (!follow || typeof EventSource === "undefined") return;
    setReconnecting(false);
    const es = new EventSource(api.logStreamUrl(sessionId));
    es.onmessage = (e) => {
      const line = JSON.parse(e.data) as LogLine;
      setReconnecting(false);
      setError(null);
      setLines((prev) => merge(prev, [line]));
    };
    es.addEventListener("end", () => {
      es.close();
      setFollow(false);
    });
    es.onerror = () => {
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
  // The marker row (n: -1) sorts first when the log was too big to read
  // whole (Kraft-2vvus); a copy past that point would buffer the same file
  // into the page the marker exists to avoid, so offer a download instead.
  const truncated = lines[0]?.n === -1 ? lines[0].truncated : undefined;

  useEffect(() => {
    if (follow && bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [shown, follow]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(await api.getLogText(sessionId));
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
      const total = tokenTotal(session);
      meta.push(
        session.cost_usd != null
          ? `${tokens(total)} tokens · ${usd(session.cost_usd)}`
          : `${tokens(total)} tokens`,
      );
    }
  }

  return (
    <div className="pane log-pane" data-testid="right-pane-log">
      {/* One row (12 · 38): Log · session id · hook · attempt · lines ·
          source chips · following · Copy · maximize. The Tasks row already
          says which session this is, so the title block that used to sit
          above the chips was saying it twice. */}
      {/* W5.5: identity and chips scroll sideways in their own track; the
          actions keep theirs, so the row stays one line at every width. */}
      <header className="log-head">
        <div className="log-head-main">
          <StatusGlyph status={session?.status ?? "unknown"} />
          <span className="log-static-title">Log</span>
          <ShortId id={sessionId} className="log-sid" />
          <span className="log-hook">{session?.hook_point ?? sessionId}</span>
          {meta.map((m) => (
            <span key={m} className="log-meta-part">{m}</span>
          ))}
          {!live && <span className="log-not-following">stopped · not following</span>}
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
        <div className="log-actions">
          <button
            className="btn btn-secondary log-follow"
            aria-pressed={follow}
            onClick={() => setFollow((v) => !v)}
          >
            <ArrowLineDown size={13} />
            {follow ? "Following" : "Follow"}
          </button>
          {truncated ? (
            <a
              className="btn btn-icon btn-ghost"
              title="Download the full log"
              href={api.logTextUrl(sessionId)}
              download
            >
              <DownloadSimple size={14} />
            </a>
          ) : (
            <button className="btn btn-icon btn-ghost" title="Copy log" onClick={copy}>
              <Copy size={14} />
            </button>
          )}
          {onToggleMaximize && (
            <button
              className="btn btn-icon btn-ghost"
              title={maximized ? "Collapse" : "Maximize"}
              aria-pressed={maximized}
              onClick={onToggleMaximize}
            >
              <ArrowsOutSimple size={14} />
            </button>
          )}
        </div>
      </header>

      <div className="log-body" ref={bodyRef}>
        {error && <p className="form-error">{error}</p>}
        {lines.length === 0 && !error && (
          <p className="empty">
            {live ? "no output yet — this session has not written a line" : "this session wrote no log"}
          </p>
        )}
        {lines.length > 0 && shown.length === 0 && !error && (
          <p className="empty">no {filter} lines — this session logged {lines.length}</p>
        )}
        {(capLines && !expanded ? shown.slice(-capLines) : shown).map((l) =>
          l.n === -1 ? (
            <div key={l.n} className="log-line log-line-truncated" data-src={l.src}>
              <span className="log-text">
                {logLineText(l)}{" "}
                <a href={api.logTextUrl(sessionId)} download>
                  download the full log
                </a>
              </span>
            </div>
          ) : (
            <div key={l.n} className="log-line" data-src={l.src}>
              <span className="log-t">{l.t ? clock(l.t) : ""}</span>
              <span className="log-src">{l.src}</span>
              <span className="log-text">{logLineText(l)}</span>
            </div>
          ),
        )}
        {capLines && !expanded && shown.length > capLines && (
          <button className="btn btn-ghost log-show-all" onClick={() => setExpanded(true)}>
            Show all {shown.length} lines
          </button>
        )}
        {follow && (
          <div className="log-following">
            <span className="live-dot" />
            {reconnecting ? "reconnecting…" : "following · new lines appear here"}
          </div>
        )}
      </div>
      {note && <p className="doc-modal-note">{note}</p>}
    </div>
  );
}
