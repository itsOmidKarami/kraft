import { useCallback, useEffect, useRef, useState } from "react";
import { detailOf, type Answer } from "../../http";
import * as api from "./itemDraftApi";
import type { DraftView, Op, Refusal } from "./types";
import { stripPassed } from "./view";

/** Ended by the server's own word for it (`display_status`): the chain does not run again. */
const ENDED = new Set(["done", "cancelled", "archived"]);
export type DraftStatus = "loading" | "ready" | "error" | "off";
type ItemLike = { id: string; display_status?: string; current_node_id: string | null };

const shaped = (b: unknown): b is DraftView => Array.isArray((b as DraftView | null)?.ops);

/** One item's draft, as the server has it (R18). It holds the last answer and
 *  nothing else. Requests go one at a time; an edit is a function of the ops in
 *  the latest answer, so two quick edits compose (Decided 2). The draft is read
 *  again on focus and when the item moves to another node or status (Decided 9). */
export function useItemDraft(item: ItemLike) {
  const { id, current_node_id: node } = item;
  const ended = ENDED.has(item.display_status ?? "");
  const [view, setView] = useState<DraftView | null>(null);
  const [status, setStatus] = useState<DraftStatus>(ended ? "off" : "loading");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const busy = useRef(0);
  const latest = useRef<DraftView | null>(null);
  const live = useRef(true);

  const take = useCallback((v: DraftView) => {
    if (!live.current) return;
    latest.current = v;
    setView(v);
    setStatus("ready");
    setError(null);
  }, []);

  const enqueue = useCallback(<T,>(fn: () => Promise<T>): Promise<T> => {
    busy.current++;
    const run = queue.current.then(fn).finally(() => { busy.current--; });
    queue.current = run.catch(() => {});
    return run;
  }, []);

  const read = useCallback(() => enqueue(async () => {
    const a = await api.getDraft(id);
    if (!live.current) return a;
    if (a.status === 200 && shaped(a.body)) take(a.body);
    else if (a.status >= 300) setStatus((s) => (s === "ready" ? s : "error"));
    return a;
  }), [id, enqueue, take]);

  useEffect(() => {
    live.current = true;
    if (ended) {
      latest.current = null;
      setView(null);
      setStatus("off");
      return;
    }
    void read();
    const onFocus = () => { if (!busy.current) void read(); };
    window.addEventListener("focus", onFocus);
    return () => {
      live.current = false;
      window.removeEventListener("focus", onFocus);
    };
  }, [ended, read, node, item.display_status]);

  /** Apply `fn` to the ops of the latest answer and send the whole list. `path` lights the pending highlight. */
  const edit = useCallback((fn: (ops: Op[]) => Op[], path?: string) => enqueue(async (): Promise<Answer<DraftView | Refusal> | null> => {
    if (!latest.current) {
      setError("The draft has not loaded yet.");
      return null;
    }
    setPending(path ?? "");
    const a = await api.putDraft(id, fn(stripPassed(latest.current.ops)));
    if (!live.current) return a;
    setPending(null);
    if (a.status === 200 && shaped(a.body)) take(a.body);
    else setError(detailOf(a.body));
    return a;
  }), [id, enqueue, take]);

  const discard = useCallback(() => enqueue(async () => {
    const a = await api.discardDraft(id);
    if (a.status === 204 || a.status === 404) {
      const g = await api.getDraft(id);
      if (live.current && g.status === 200 && shaped(g.body)) take(g.body);
    } else if (live.current) setError(detailOf(a.body));
    return a;
  }), [id, enqueue, take]);

  /** The answer is the caller's to read: 200 (draft gone), 409 (passed), 422 (problems), 404. A refusal reads the draft again. */
  const apply = useCallback(() => enqueue(async () => {
    const a = await api.applyDraft(id);
    if (!live.current) return a;
    if (a.status === 200 && shaped(a.body)) take(a.body);
    else if (a.status === 409 || a.status === 422 || a.status === 404) {
      const g = await api.getDraft(id);
      if (live.current && g.status === 200 && shaped(g.body)) take(g.body);
    }
    return a;
  }), [id, enqueue, take]);

  return { status, view, error, pending, edit, discard, apply, reload: read };
}
export type ItemDraft = ReturnType<typeof useItemDraft>;
