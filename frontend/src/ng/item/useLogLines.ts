import { useEffect, useState } from "react";
import type { LogLine } from "../../types";
import { logUrl } from "../../api";
import { request } from "../http";

export const POLL_MS = 3000;

/** A session's log lines, read again every few seconds while it runs: the Log pane's and the failed card's tail.
 *  `lines` is null until the first read lands; `error` says why a read did not. */
export function useLogLines(workItemId: string, sessionId: string, running: boolean): { lines: LogLine[] | null; error: string | null } {
  const [lines, setLines] = useState<LogLine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const read = () =>
      request<{ lines: LogLine[] }>(`${logUrl(workItemId, sessionId)}?format=jsonl`).then((r) => {
        if (!live) return;
        // A pending attempt has no log yet: once it runs, a read that succeeds clears the note.
        if (r.status === 200) { setLines(r.body.lines); setError(null); }
        else setError(r.status === 404 ? "No log for this attempt yet." : "The log could not be read.");
      });
    read();
    const t = running ? setInterval(read, POLL_MS) : undefined;
    return () => { live = false; if (t) clearInterval(t); };
  }, [workItemId, sessionId, running]);
  return { lines, error };
}
