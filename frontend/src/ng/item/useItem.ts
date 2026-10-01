import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../../api";
import { useStore } from "../../store";
import type { WorkItem, WorkerSession } from "../../types";

export type ItemDetail = WorkItem & { worker_sessions: WorkerSession[] };
export type Loaded = { state: "loading" } | { state: "missing"; error: string } | { state: "ready"; item: ItemDetail };

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

  const fetchNow = useCallback(() => {
    api
      .getWorkItem(id)
      .then((item) => live.current === id && setLoaded({ state: "ready", item }))
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
