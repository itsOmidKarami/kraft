import { useCallback, useEffect, useRef, useState } from "react";
import { useStore } from "../../store";
import type { Compare, CompareTarget, ReviewThread } from "../../types";
import { detailOf, request } from "../http";
import { COALESCE_MS } from "../item/useItem";

export type Fetched<T> = { state: "loading" } | { state: "error"; status: number; error: string } | { state: "ready"; data: T };

/** `GET /compare` for one pair of targets. Read again when either target or
 *  the whitespace flag changes, and when the item's HEAD moves (a new commit
 *  changes `latest`). */
export function useCompare(id: string, from: CompareTarget, to: CompareTarget, ignoreWhitespace: boolean, head: string | null | undefined) {
  const [got, setGot] = useState<Fetched<Compare>>({ state: "loading" });
  useEffect(() => {
    let live = true;
    const q = new URLSearchParams({ from, to });
    if (ignoreWhitespace) q.set("ignore_whitespace", "1");
    request<Compare>(`/work-items/${encodeURIComponent(id)}/compare?${q}`).then(({ status, body }) => {
      if (!live) return;
      setGot(status === 200 ? { state: "ready", data: body } : { state: "error", status, error: detailOf(body) });
    });
    return () => void (live = false);
  }, [id, from, to, ignoreWhitespace, head]);
  return got;
}

/** The events after which the item's threads may have changed. */
const THREAD_EVENTS = /^(thread_updated|review_submitted|rewind_requested|rewind_cancelled|reply_agent_.+)$/;

/** `GET /work-items/{id}/threads`, read again after each burst of this item's
 *  thread events. `reload` is for after the page's own write. */
export function useThreads(id: string) {
  const [got, setGot] = useState<Fetched<ReviewThread[]>>({ state: "loading" });
  const relevant = useStore((s) => (s.eventsByItem[id] ?? []).filter((e) => THREAD_EVENTS.test(e.type)).length);
  const live = useRef(id);
  live.current = id;
  const fetchNow = useCallback(() => {
    request<ReviewThread[]>(`/work-items/${encodeURIComponent(id)}/threads`).then(({ status, body }) => {
      if (live.current !== id) return;
      if (status === 200) setGot({ state: "ready", data: body });
      else setGot((g) => (g.state === "ready" ? g : { state: "error", status, error: detailOf(body) }));
    });
  }, [id]);
  useEffect(fetchNow, [fetchNow]);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) return void (first.current = false);
    const t = setTimeout(fetchNow, COALESCE_MS);
    return () => clearTimeout(t);
  }, [relevant, fetchNow]);
  return { ...got, reload: fetchNow };
}
