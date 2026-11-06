import { useEffect, useState } from "react";
import { subagentsUrl } from "../../api";
import type { Subagent } from "../../types";
import { request } from "../http";
import { POLL_MS } from "./useLogLines";

/** What a list of sub-agents comes to, for the Overview's count line and the fact row. */
export function subagentCount(list: Subagent[]): { running: number; finished: number; cutOff: number } {
  const running = list.filter((a) => a.status === "running").length;
  const cutOff = list.filter((a) => a.status === "cut_off").length;
  return { running, finished: list.length - running - cutOff, cutOff };
}

const read = (workItemId: string, sessionId: string) =>
  request<{ subagents: Subagent[] }>(subagentsUrl(workItemId, sessionId)).then((r) => (r.status === 200 ? r.body.subagents : null));

/** One session's sub-agents: read again every few seconds while it runs and the page is visible, once otherwise.
 *  Null until the first read lands, when a read fails, and always when `enabled` is false (a session that is no agent's). */
export function useSessionSubagents(workItemId: string, sessionId: string, running: boolean, enabled = true): Subagent[] | null {
  const [list, setList] = useState<Subagent[] | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    const load = () => read(workItemId, sessionId).then((l) => { if (live && l) setList(l); });
    load();
    const t = running ? setInterval(() => { if (document.visibilityState === "visible") void load(); }, POLL_MS) : undefined;
    return () => { live = false; if (t) clearInterval(t); };
  }, [workItemId, sessionId, running, enabled]);
  return enabled ? list : null;
}
