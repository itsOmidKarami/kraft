import { useCallback, useEffect, useRef, useState } from "react";
import { useStore } from "../../store";
import type { Compare, CompareTarget, ReviewThread, WorkItem, WorkItemArtifact } from "../../types";
import { detailOf, request } from "../http";
import { COALESCE_MS } from "../item/useItem";

export type Fetched<T> = { state: "loading" } | { state: "error"; status: number; error: string } | { state: "ready"; data: T };

/** `GET /compare` for one pair of targets. Read again when either target or
 *  the whitespace flag changes, and when the item's HEAD moves (a new commit
 *  changes `latest`). */
export function useCompare(id: string, from: CompareTarget, to: CompareTarget, ignoreWhitespace: boolean, head: string | null | undefined, skip = false) {
  const [got, setGot] = useState<Fetched<Compare>>({ state: "loading" });
  useEffect(() => {
    // `skip`: there is nothing to compare yet (a never-started item has no worktree, and the server answers 409).
    if (skip) return;
    let live = true;
    const q = new URLSearchParams({ from, to });
    if (ignoreWhitespace) q.set("ignore_whitespace", "1");
    request<Compare>(`/work-items/${encodeURIComponent(id)}/compare?${q}`).then(({ status, body }) => {
      if (!live) return;
      setGot(status === 200 ? { state: "ready", data: body } : { state: "error", status, error: detailOf(body) });
    });
    return () => void (live = false);
  }, [id, from, to, ignoreWhitespace, head, skip]);
  return got;
}

/** How often an open review reads its threads again with no event to say so. */
export const THREADS_POLL_MS = 15_000;

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
  // A draft added from elsewhere (`kraft item comment`, another tab) writes no
  // event, so the threads are read again when the page comes back into view,
  // and every so often while it is shown (R9b-15): the bar counts pending
  // comments, and Approve lists them.
  useEffect(() => {
    const again = () => {
      if (document.visibilityState !== "hidden") fetchNow();
    };
    window.addEventListener("focus", again);
    document.addEventListener("visibilitychange", again);
    const t = setInterval(again, THREADS_POLL_MS);
    return () => {
      window.removeEventListener("focus", again);
      document.removeEventListener("visibilitychange", again);
      clearInterval(t);
    };
  }, [fetchNow]);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) return void (first.current = false);
    const t = setTimeout(fetchNow, COALESCE_MS);
    return () => clearTimeout(t);
  }, [relevant, fetchNow]);
  return { ...got, reload: fetchNow };
}

/** Viewed marks (B11): the comparison's own, overlaid by what was clicked since
 *  it loaded. A click is sent at once and taken back, with the server's words,
 *  if refused. */
export function useViewed(id: string, to: CompareTarget, compare: Fetched<Compare>) {
  const [marks, setMarks] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setMarks({}), [compare]);
  const files = compare.state === "ready" ? compare.data.files : [];
  const isViewed = (path: string) => marks[path] ?? files.find((f) => f.path === path)?.viewed ?? false;
  const toggle = async (path: string, viewed: boolean) => {
    setMarks((m) => ({ ...m, [path]: viewed }));
    const q = new URLSearchParams({ file: path, to });
    const { status, body } = await request(`/work-items/${encodeURIComponent(id)}/viewed?${q}`, { method: viewed ? "PUT" : "DELETE" });
    if (status === 200) return setError(null);
    setMarks((m) => ({ ...m, [path]: !viewed }));
    setError(detailOf(body));
  };
  return { isViewed, toggle, error };
}

/** The pending gate's document (GET /artifact), read while there is one: the
 *  gate review shows it, and a chain revision's `digest` rides on Approve. */
export function useArtifact(item: Pick<WorkItem, "id" | "pending_gate" | "gate_artifact" | "updated_at">) {
  const [got, setGot] = useState<Fetched<WorkItemArtifact> | null>(null);
  const wanted = !!(item.pending_gate && item.gate_artifact);
  useEffect(() => {
    if (!wanted) return setGot(null);
    let live = true;
    request<WorkItemArtifact>(`/work-items/${encodeURIComponent(item.id)}/artifact`).then(({ status, body }) => {
      if (live) setGot(status === 200 ? { state: "ready", data: body } : { state: "error", status, error: detailOf(body) });
    });
    return () => void (live = false);
  }, [item.id, wanted, item.pending_gate, item.updated_at]);
  return got;
}
