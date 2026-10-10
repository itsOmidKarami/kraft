import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../../store";
import type { Compare, CompareTarget, ReviewThread, WorkItem, WorkItemArtifact } from "../../types";
import { detailOf, request } from "../http";
import { COALESCE_MS } from "../item/useItem";
import { showToast } from "../ui/Toast";
import { grow, reveal, spansOf, widen, type Grow, type Span, type Whole } from "./expand";
import { parsePatch, type PatchFile } from "./patch";
import { threadRange, type LineRange } from "./range";

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

/** The server's most context lines (`CONTEXT_MAX_LINES`): a whole file. */
const WHOLE_FILE = 1_000_000;

/** The unchanged lines a hunk row's arrows show: a file's whole diff is read once
 *  (`/compare?file=&context=`) and `patch` comes back with that file drawn wider.
 *  A thread on a line the diff leaves out has its file read too, and its lines
 *  drawn, so it sits under them. A new `patch` (another comparison, a new commit) starts over. */
export function useExpanded(id: string, from: CompareTarget, to: CompareTarget, ignoreWhitespace: boolean, patch: Map<string, PatchFile>, threads: ReviewThread[]) {
  const [got, setGot] = useState<{ of: Map<string, PatchFile>; files: Map<string, Whole> }>({ of: patch, files: new Map() });
  const live = useRef(patch);
  live.current = patch;
  // This comparison's files being read (a press on one of them is dropped: it names gaps of a drawing about to
  // change), and those read for their threads, each once: one that cannot be read leaves its threads in the list at the foot.
  const work = useRef({ of: patch, reading: new Set<string>(), tried: new Set<string>() });
  if (work.current.of !== patch) work.current = { of: patch, reading: new Set(), tried: new Set() };
  const { reading, tried } = work.current;
  const files = got.of === patch ? got.files : null;
  /** The whole file, or null with the reason said (not when `quiet`: nobody asked). */
  const read = async (path: string, quiet = false): Promise<Whole | null> => {
    if (reading.has(path)) return null;
    reading.add(path);
    const q = new URLSearchParams({ from, to, file: path, context: String(WHOLE_FILE) });
    if (ignoreWhitespace) q.set("ignore_whitespace", "1");
    const { status, body } = await request<Compare>(`/work-items/${encodeURIComponent(id)}/compare?${q}`);
    reading.delete(path);
    if (live.current !== patch) return null;
    const pf = patch.get(path);
    const got = status === 200 && !body.truncated ? parsePatch(body.diff).find((f) => f.path === path) : undefined;
    const whole = pf && got ? spansOf(pf, got) : null;
    // Cut at the server's size cap, the file would seem to end where the cut fell.
    if (!whole && !quiet) showToast(status !== 200 ? detailOf(body) : body.truncated ? `${path} is too large to show in full` : `Could not read the rest of ${path}`);
    return whole;
  };
  /** `path` drawn as `next` makes of its spans as they are now: a second press before the first drew adds to it. */
  const draw = (path: string, first: Whole, next: (w: Whole) => Span[]) =>
    setGot((g) => {
      const had = g.of === patch ? g.files : new Map<string, Whole>();
      const w = had.get(path) ?? first;
      const spans = next(w);
      return spans === w.spans && had.has(path) ? g : { of: patch, files: new Map(had).set(path, { lines: w.lines, spans }) };
    });
  const expand = async (path: string, gap: number, how: Grow) => {
    const whole = files?.get(path) ?? (await read(path));
    if (whole) draw(path, whole, (w) => grow(w.spans, gap, how, w.lines.length));
  };
  const wide = useMemo(() => (files ? new Map([...patch].map(([k, pf]) => [k, files.has(k) ? widen(pf, files.get(k)!) : pf])) : patch), [patch, files]);
  useEffect(() => {
    const undrawn = new Map<string, LineRange[]>();
    for (const t of threads) {
      const r = threadRange(t);
      const pf = wide.get(t.file_path ?? "");
      if (!r || !pf?.hunks.length || pf.hunks.some((h) => h.lines.some((l) => l[r.side] === r.end))) continue;
      undrawn.set(pf.path, [...(undrawn.get(pf.path) ?? []), r]);
    }
    for (const [path, ranges] of undrawn) {
      const show = (w: Whole) => ranges.reduce((spans, r) => reveal({ lines: w.lines, spans }, r), w.spans);
      const whole = files?.get(path);
      if (whole) draw(path, whole, show);
      else if (!tried.has(path)) {
        tried.add(path);
        void read(path, true).then((w) => w && draw(path, w, show));
      }
    }
    // `read` and `draw` are of this render's `patch`, which `wide` follows.
  }, [wide, threads]);
  return { patch: wide, expand };
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
  // The last answer as read, so a poll that brings nothing new re-renders nothing (review L6).
  const last = useRef<string | null>(null);
  const fetchNow = useCallback(() => {
    request<ReviewThread[]>(`/work-items/${encodeURIComponent(id)}/threads`).then(({ status, body }) => {
      if (live.current !== id) return;
      if (status === 200) {
        const text = `${id}\n${JSON.stringify(body)}`;
        if (text === last.current) return;
        last.current = text;
        setGot({ state: "ready", data: body });
      } else setGot((g) => (g.state === "ready" ? g : { state: "error", status, error: detailOf(body) }));
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
