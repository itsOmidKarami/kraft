import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../../api";
import { useStore } from "../../store";
import type { WorkItem, WorkerSession } from "../../types";

export type ItemDetail = WorkItem & { worker_sessions: WorkerSession[] };
/** `version` moves on every read of the item. Key what the page reads beside the
 *  item (its events, documents, thread, diff) on it, not on `updated_at`: the
 *  server leaves `updated_at` alone when only a session or an event changes, so
 *  a new attempt or a reply in the thread would never reach them. */
export type Loaded = { state: "loading" } | { state: "missing"; error: string } | { state: "ready"; item: ItemDetail; version: string };

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
  const reads = useRef(0);

  const fetchNow = useCallback(() => {
    api
      .getWorkItem(id)
      .then((item) => live.current === id && setLoaded({ state: "ready", item, version: String(++reads.current) }))
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
