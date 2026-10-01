import { useEffect, useState } from "react";
import type { LogLine } from "../../../types";
import { request } from "../../http";

export const LOG_POLL_MS = 3000;

/** A worker session's log (`GET /worker-sessions/{id}/log?format=jsonl`), read
 *  again every few seconds while the session runs. The same read as the desktop
 *  Log pane, which keeps its own inside the pane. */
export function useLog(sessionId: string | null, running: boolean) {
  const [lines, setLines] = useState<LogLine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setLines(null);
    setError(null);
    if (!sessionId) return;
    let live = true;
    const read = () =>
      request<{ lines: LogLine[] }>(`/worker-sessions/${encodeURIComponent(sessionId)}/log?format=jsonl`).then((r) => {
        if (!live) return;
        if (r.status === 200) setLines(r.body.lines.filter((l) => !l.truncated));
        else setError(r.status === 404 ? "No log for this attempt yet." : "The log could not be read.");
      });
    void read();
    const t = running ? setInterval(read, LOG_POLL_MS) : undefined;
    return () => {
      live = false;
      if (t) clearInterval(t);
    };
  }, [sessionId, running]);
  return { lines, error };
}
