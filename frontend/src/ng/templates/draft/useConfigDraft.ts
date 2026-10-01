import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Answer } from "../../http";
import { detailOf } from "../../http";
import * as d from "./draftApi";
import type { Area, DraftView, Op, OpsView, StaleBody } from "./types";
import { normalise, type NodeA } from "./view";

export const TEXT_DEBOUNCE_MS = 300;
export const FIELD_PAUSE_MS = 800;

export type DraftStatus = "loading" | "ready" | "notFound" | "error";

/** One config draft, as the server has it (spec §6.3, R18). The hook holds the
 *  last answer and nothing else, except the last resolved steps of each node it
 *  rendered (brief Decided 2). Requests to the draft go one at a time, in order
 *  (Decided 3); a refusal's detail is `error` until the next answer. */
export function useConfigDraft(area: Area, key: string) {
  const [view, setView] = useState<DraftView | null>(null);
  const [status, setStatus] = useState<DraftStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Op[] | null>(null);
  const [stale, setStale] = useState<StaleBody | null>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const busy = useRef(0);
  const lastResolved = useRef(new Map<string, NodeA>());
  const typed = useRef(new Map<string, { timer: ReturnType<typeof setTimeout>; send: () => void }>());
  const live = useRef(true);

  const take = useCallback((v: DraftView) => {
    if (!live.current) return;
    const nodes = v.result.resolved?.chain.nodes as NodeA[] | undefined;
    nodes?.forEach((n) => lastResolved.current.set(n.id, normalise(n) as NodeA));
    setView(v);
    setStatus("ready");
    setError(null);
  }, []);

  /** Runs `fn` after every earlier request; a write's answer replaces the view. */
  const enqueue = useCallback(<T,>(fn: () => Promise<Answer<T>>, write: boolean, keep = true): Promise<Answer<T>> => {
    busy.current++;
    const run = queue.current.then(fn).then((a) => {
      if (keep && a.status >= 200 && a.status < 300 && a.body && typeof a.body === "object" && "result" in (a.body as object)) take(a.body as unknown as DraftView);
      else if (keep && a.status >= 300 && live.current) setError(detailOf(a.body));
      if (write) d.draftsChanged();
      return a;
    }).finally(() => { busy.current--; });
    queue.current = run.catch(() => {});
    return run;
  }, [take]);

  const load = useCallback(() => enqueue(async () => {
    const a = await d.getDraft(area, key);
    if (a.status === 404 && live.current) setStatus("notFound");
    else if (a.status >= 300 && live.current) setStatus("error");
    return a;
  }, false), [area, key, enqueue]);

  useEffect(() => {
    live.current = true;
    setView(null);
    setStatus("loading");
    setStale(null);
    lastResolved.current = new Map();
    load();
    return () => {
      live.current = false;
    };
  }, [load]);

  // Typed fields: one set_field per pause (Decided 3).
  const flushTyped = useCallback(() => {
    const all = [...typed.current.values()];
    typed.current.clear();
    for (const t of all) {
      clearTimeout(t.timer);
      t.send();
    }
  }, []);

  // The YAML view: one PUT 300 ms after typing stops (spec §6.3).
  const textTimer = useRef<{ timer: ReturnType<typeof setTimeout>; send: () => void } | null>(null);
  const flushText = useCallback(() => {
    const t = textTimer.current;
    textTimer.current = null;
    if (t) {
      clearTimeout(t.timer);
      t.send();
    }
  }, []);
  const text = useCallback((file: string, body: string) => {
    if (textTimer.current) clearTimeout(textTimer.current.timer);
    const send = () => void enqueue(() => d.putFile(area, key, file, body), true);
    textTimer.current = { send, timer: setTimeout(() => { textTimer.current = null; send(); }, TEXT_DEBOUNCE_MS) };
  }, [area, key, enqueue]);

  const ops = useCallback((list: Op[], opts: { preview?: boolean } = {}): Promise<Answer<OpsView>> => {
    if (!opts.preview) {
      flushTyped();
      flushText();
    }
    return enqueue(async () => {
      if (!opts.preview) setPending(list);
      try {
        return await d.postOps(area, key, list, opts.preview);
      } finally {
        if (!opts.preview && live.current) setPending(null);
      }
    }, !opts.preview, !opts.preview);
  }, [area, key, enqueue, flushTyped, flushText]);

  const field = useCallback((path: string, name: string, value: unknown, pause = false) => {
    const op: Op = { op: "set_field", path, field: name, value };
    if (!pause) return void ops([op]);
    const id = `${path}#${name}`;
    const prev = typed.current.get(id);
    if (prev) clearTimeout(prev.timer);
    const send = () => void enqueue(() => d.postOps(area, key, [op]), true);
    typed.current.set(id, { send, timer: setTimeout(() => { typed.current.delete(id); send(); }, FIELD_PAUSE_MS) });
  }, [area, key, enqueue, ops]);

  const flush = useCallback(() => { flushTyped(); flushText(); }, [flushTyped, flushText]);
  useEffect(() => flush, [flush]);

  const undo = useCallback(() => {
    flush();
    return enqueue(async () => {
      const a = await d.undo(area, key);
      if (a.status === 409) return { ...a, body: { detail: "Nothing to undo" } as unknown as DraftView };
      return a;
    }, true);
  }, [area, key, enqueue, flush]);

  const publish = useCallback(() => {
    flush();
    return enqueue(async () => {
      const a = await d.publish(area, key);
      if (a.status === 409 && live.current) setStale(a.body as unknown as StaleBody);
      if (a.status === 200 && live.current) setStale(null);
      return a;
    }, true).then(async (a) => {
      if (a.status === 200) await load();
      return a;
    });
  }, [area, key, enqueue, flush, load]);

  const discard = useCallback(() => {
    flush();
    return enqueue(() => d.discard(area, key), true).then(async (a) => {
      if (a.status === 204 || a.status === 404) {
        setStale(null);
        await load();
      }
      return a;
    });
  }, [area, key, enqueue, flush, load]);

  // Another tab may have changed the draft (spec §6.3): refetch on focus when idle.
  useEffect(() => {
    const onFocus = () => {
      if (busy.current === 0 && !textTimer.current && typed.current.size === 0) load();
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [load]);

  /** A node's steps: the resolved chain's, else the last this page rendered (Decided 2). */
  const resolvedNode = useCallback((id: string): NodeA | null => {
    const n = (view?.result.resolved?.chain.nodes as NodeA[] | undefined)?.find((x) => x.id === id);
    return n ? (normalise(n) as NodeA) : lastResolved.current.get(id) ?? null;
  }, [view]);

  return useMemo(() => ({
    view, status, error, pending, stale,
    clearError: () => setError(null),
    ops, field, text, flush, undo, publish, discard, reload: load, resolvedNode,
  }), [view, status, error, pending, stale, ops, field, text, flush, undo, publish, discard, load, resolvedNode]);
}

export type ConfigDraft = ReturnType<typeof useConfigDraft>;
