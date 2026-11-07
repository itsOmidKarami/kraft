import { useEffect, useRef, useState } from "react";
import { subagentsUrl, subagentUrl } from "../../api";
import type { Subagent, SubagentDetail } from "../../types";
import { request } from "../http";
import type { ItemDetail } from "./useItem";
import { POLL_MS } from "./useLogLines";

const read = (workItemId: string, sessionId: string) =>
  request<{ subagents: Subagent[] }>(subagentsUrl(workItemId, sessionId)).then((r) => (r.status === 200 ? r.body.subagents : null));

/** One session's sub-agents: read again every few seconds while it runs and the page is visible, once otherwise.
 *  Null until the first read lands, when a read fails, and always when `enabled` is false (a session that is no agent's). */
export function useSessionSubagents(workItemId: string, sessionId: string, running: boolean, enabled = true): Subagent[] | null {
  const [list, setList] = useState<Subagent[] | null>(null);
  useEffect(() => {
    setList(null);
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

/** The sub-agents the item page draws, by session id: the running agent sessions' as `useRunningSubagents` reads them,
 *  and each finished agent session of `nodes` read once and kept, never every session of the item. A session that
 *  stops keeps its last list until its own read lands, so its marks do not blink out as it finishes. */
export function useSubagentLists(item: ItemDetail, nodes: (string | undefined)[]): Record<string, Subagent[]> {
  const live = useRunningSubagents(item);
  const last = useRef<Record<string, Subagent[]>>({});
  Object.assign(last.current, live);
  const [done, setDone] = useState<Record<string, Subagent[]>>({});
  const asked = useRef(new Set<string>());
  const key = item.worker_sessions.filter((s) => nodes.includes(s.node_id) && s.harness != null && s.status !== "running" && s.status !== "pending").map((s) => s.id).join("\n");
  const ids = key ? key.split("\n") : [];
  useEffect(() => {
    for (const id of ids) {
      if (asked.current.has(id)) continue;
      asked.current.add(id);
      // A read that fails is asked again once the sessions shown change, not left out for good.
      void read(item.id, id).then((l) => (l ? setDone((d) => ({ ...d, [id]: l })) : asked.current.delete(id)));
    }
  }, [item.id, key]); // eslint-disable-line react-hooks/exhaustive-deps
  // A stopped session's last list stands only until its own read lands: one of a node not shown is dropped, as
  // `useRunningSubagents` drops it, or its sub-agents would read as running for good.
  const kept = Object.fromEntries(ids.flatMap((id) => (last.current[id] ? [[id, last.current[id]]] : [])));
  return { ...kept, ...done, ...live };
}

/** One sub-agent with its whole prompt, read when `load` is called (a disclosure opening) and not before. */
export function useSubagentDetail(workItemId: string, sessionId: string, agent: string): { detail: SubagentDetail | "failed" | null; load: () => void } {
  const [detail, setDetail] = useState<SubagentDetail | "failed" | null>(null);
  const load = () => {
    if (detail && detail !== "failed") return;
    void request<SubagentDetail>(subagentUrl(workItemId, sessionId, agent)).then((r) => setDetail(r.status === 200 ? r.body : "failed"));
  };
  return { detail, load };
}
