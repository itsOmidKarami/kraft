import { useEffect, useRef, useState } from "react";
import { clock, logLineText } from "../../../format";
import type { LogLine } from "../../../types";
import { Maximize2 } from "../../icons";
import { Dialog } from "../../ui/Dialog";
import { request } from "../../http";

const SOURCES = ["sys", "stdout", "agent", "tool"] as const;
export const POLL_MS = 3000;

/** A session's log (GAP §2 #4, #40): source chips, follow, copy, the whole
 *  file when the view was cut, full screen. Read again every few seconds
 *  while the session runs. */
export function Log({ sessionId, running, title }: { sessionId: string; running: boolean; title: string }) {
  const [lines, setLines] = useState<LogLine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [off, setOff] = useState<Set<string>>(new Set());
  const [follow, setFollow] = useState(true);
  const [full, setFull] = useState(false);
  useEffect(() => {
    let live = true;
    const read = () =>
      request<{ lines: LogLine[] }>(`/worker-sessions/${encodeURIComponent(sessionId)}/log?format=jsonl`).then((r) => {
        if (!live) return;
        // A pending attempt has no log yet: once it runs, a read that succeeds clears the note.
        if (r.status === 200) { setLines(r.body.lines); setError(null); }
        else setError(r.status === 404 ? "No log for this attempt yet." : "The log could not be read.");
      });
    read();
    const t = running ? setInterval(read, POLL_MS) : undefined;
    return () => { live = false; if (t) clearInterval(t); };
  }, [sessionId, running]);

  const cut = lines?.find((l) => l.truncated);
  const shown = (lines ?? []).filter((l) => !l.truncated && !off.has(l.src));
  const body = (big: boolean) => <LogBody lines={shown} follow={follow} big={big} />;
  const toggle = (s: string) => setOff((o) => { const n = new Set(o); if (n.has(s)) n.delete(s); else n.add(s); return n; });
  const head = (
    <div className="ip-log-head">
      <span className="item-muted">{lines ? `${shown.length} lines` : "Reading…"}</span>
      <span className="ip-chips" role="group" aria-label="Sources">
        {SOURCES.map((s) => <button key={s} type="button" className="ip-chip" aria-pressed={!off.has(s)} onClick={() => toggle(s)}>{s}</button>)}
      </span>
      <label className="item-check ip-follow"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow</label>
      <button type="button" className="item-link" onClick={() => navigator.clipboard?.writeText(shown.map(logLineText).join("\n"))}>copy</button>
      {!full && <button type="button" className="item-link ip-full" onClick={() => setFull(true)}><Maximize2 size={11} aria-hidden /> full screen</button>}
    </div>
  );
  if (error) return <p className="item-muted">{error}</p>;
  return (
    <>
      {head}
      {cut?.truncated && (
        <p className="item-muted">
          {cut.truncated.lines} earlier lines are not shown. <a className="item-link" href={`/api/worker-sessions/${encodeURIComponent(sessionId)}/log`} download={`${sessionId}.log`}>Download the whole log</a>
        </p>
      )}
      {body(false)}
      {full && (
        <Dialog title={`${title} · log`} onClose={() => setFull(false)}>
          <div className="ip-log-full">{head}{body(true)}</div>
        </Dialog>
      )}
    </>
  );
}

function LogBody({ lines, follow, big }: { lines: LogLine[]; follow: boolean; big: boolean }) {
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (follow && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [lines, follow]);
  return (
    <pre ref={ref} className={`ip-log${big ? " is-big" : ""}`} tabIndex={0} aria-label="Log lines">
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
