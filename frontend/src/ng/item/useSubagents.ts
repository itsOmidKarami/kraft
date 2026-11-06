import { useEffect, useState } from "react";
import { subagentsUrl } from "../../api";
import type { Subagent } from "../../types";
import { request } from "../http";
import type { ItemDetail } from "./useItem";
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

/** The sub-agents of each of the item's running agent sessions, by session id: one poll per session that runs and names a
 *  harness (a test command's does not), every few seconds while the page is visible. A session that stops leaves the map. */
export function useRunningSubagents(item: ItemDetail): Record<string, Subagent[]> {
  const ids = item.worker_sessions.filter((s) => s.status === "running" && s.harness != null).map((s) => s.id);
  const key = ids.join("\n");
  const [found, setFound] = useState<Record<string, Subagent[]>>({});
  useEffect(() => {
    if (!key) return;
    let live = true;
    const load = () => {
      for (const id of key.split("\n")) void read(item.id, id).then((l) => { if (live && l) setFound((f) => ({ ...f, [id]: l })); });
    };
    load();
    const t = setInterval(() => { if (document.visibilityState === "visible") load(); }, POLL_MS);
    return () => { live = false; clearInterval(t); };
  }, [item.id, key]);
  return Object.fromEntries(ids.flatMap((id) => (found[id] ? [[id, found[id]]] : [])));
}
