import { useEffect, useState } from "react";
import type { LogLine } from "../../../types";
import { logUrl } from "../../../api";
import { request } from "../../http";
import { LOG_POLL_MS } from "./useLog";

export interface TaggedLine extends LogLine {
  who: string;
}

/** Several sessions' logs as one list, a session after the other, each line named for its task (the node screen's Log).
 *  Every one is a session of `workItemId`. */
export function useLogs(workItemId: string, sessions: { id: string; who: string; running: boolean }[]) {
  const [lines, setLines] = useState<TaggedLine[] | null>(null);
  const key = sessions.map((s) => `${s.id}:${s.running}`).join(",");
  const anyRunning = sessions.some((s) => s.running);
  useEffect(() => {
    setLines(null);
    if (!sessions.length) return setLines([]);
    let live = true;
    const read = async () => {
      const all = await Promise.all(
        sessions.map((s) =>
          request<{ lines: LogLine[] }>(`${logUrl(workItemId, s.id)}?format=jsonl`).then((r) =>
            r.status === 200 ? r.body.lines.filter((l) => !l.truncated).map((l): TaggedLine => ({ ...l, who: s.who })) : [],
          ),
        ),
      );
      if (live) setLines(all.flat());
    };
    void read();
    const t = anyRunning ? setInterval(read, LOG_POLL_MS) : undefined;
    return () => {
      live = false;
      if (t) clearInterval(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workItemId, key, anyRunning]);
  return lines;
}
