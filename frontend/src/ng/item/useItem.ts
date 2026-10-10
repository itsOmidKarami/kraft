import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../../api";
import { useStore } from "../../store";
import type { WorkItem, WorkerSession } from "../../types";

export type ItemDetail = WorkItem & {
  worker_sessions: WorkerSession[];
  /** Set by `asOfPass` alone, never by the server: the node whose earlier pass this copy of the item shows, and which pass. */
  earlier_pass?: { node: string; pass: number };
};
/** `version` moves when a read of the item brings something new. Key what the
 *  page reads beside the item (its events, documents, thread, diff) on it, not
 *  on `updated_at`: the server leaves `updated_at` alone when only a session or
 *  an event changes, so a new attempt or a reply in the thread would never
 *  reach them. */
export type Loaded = { state: "loading" } | { state: "missing"; error: string } | { state: "ready"; item: ItemDetail; version: string };

/** The item's row and each session's status, as one string. What sessions
 *  write (documents, the diff) is read again when it moves: a new attempt, one
 *  that ends, a node that moves on. */
export function runVersion(item: ItemDetail): string {
  return `${item.updated_at}|${item.worker_sessions.map((s) => `${s.id}:${s.status}`).join(",")}`;
}

/** What a read of the item holds, as one string: its `runVersion` and the
 *  item's newest live event the read was taken after (`seq`). A read that
 *  brings nothing new (the page's own `reload` after an action whose events
 *  have not landed yet) gives the same string, so nothing keyed on it reads
 *  again. */
export function versionOf(item: ItemDetail, seq: number): string {
  return `${runVersion(item)}|${seq}`;
}

/** How long a burst of events is gathered before the one refetch it causes. */
export const COALESCE_MS = 120;

/** The item page's one loader: `GET /work-items/{id}`, read again after each
 *  burst of this item's events. Components read its result; none fetch the
 *  item themselves. `reload` is for after an action of the page's own. */
export function useItem(id: string): Loaded & { reload: () => void } {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const events = useStore((s) => s.eventsByItem[id]?.length ?? 0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const live = useRef(id);
  live.current = id;
  // The newest live event this item has had, read when each fetch starts.
  const seq = useRef(0);
  seq.current = useStore((s) => s.eventsByItem[id]?.at(-1)?.seq ?? 0);

  const fetchNow = useCallback(() => {
    const after = seq.current;
    api
      .getWorkItem(id)
      .then((item) => live.current === id && setLoaded({ state: "ready", item, version: versionOf(item, after) }))
      .catch((e: Error) => live.current === id && setLoaded((l) => (l.state === "ready" ? l : { state: "missing", error: e.message })));
  }, [id]);

  useEffect(() => {
    setLoaded({ state: "loading" });
    fetchNow();
  }, [fetchNow]);

  const first = useRef(true);
  useEffect(() => {
    if (first.current) return void (first.current = false);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(fetchNow, COALESCE_MS);
  }, [events, fetchNow]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  return { ...loaded, reload: fetchNow };
}
