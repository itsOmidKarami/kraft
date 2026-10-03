import { useEffect, useRef, useState } from "react";
import type { KraftEvent } from "../../types";
import { request } from "../http";

/** The newest events of an item, oldest first: the last RECENT, paged
 *  backwards (B13), then topped up with what came after the newest one held
 *  each time `version` moves. The chain pane's Recent and who passed each gate. */
export const RECENT = 100;
export function useEvents(id: string, version: string): KraftEvent[] {
  const [events, setEvents] = useState<KraftEvent[]>([]);
  const held = useRef<{ id: string; events: KraftEvent[] } | null>(null);
  useEffect(() => {
    let live = true;
    const have = held.current?.id === id ? held.current.events : null;
    const last = have?.at(-1)?.seq;
    // before_seq past any real seq: the last RECENT events, oldest first.
    const page = last == null ? `before_seq=${2 ** 31 - 1}&limit=${RECENT}` : `after_seq=${last}`;
    request<KraftEvent[]>(`/work-items/${encodeURIComponent(id)}/events?${page}`).then((r) => {
      if (!live || r.status !== 200 || !Array.isArray(r.body)) return;
      const more = last == null ? r.body : r.body.filter((e) => e.seq > last);
      if (have && last != null && !more.length) return;
      held.current = { id, events: last == null ? more : [...have!, ...more].slice(-RECENT) };
      setEvents(held.current.events);
    });
    return () => { live = false; };
  }, [id, version]);
  return events;
}

/** Every event of an item, oldest first: read whole once, then only what came
 *  after the newest one held, each time `version` moves (events are only ever
 *  appended, in seq order). Null until the first read lands. A read the
 *  effect has moved past is dropped, so an older answer never wins. Off
 *  (`on` false), it reads nothing and keeps what it holds. */
export function useEventLog(id: string, version: string, on = true): KraftEvent[] | null {
  const [log, setLog] = useState<{ id: string; events: KraftEvent[] } | null>(null);
  const held = useRef(log);
  useEffect(() => {
    if (!on) return;
    let live = true;
    const have = held.current?.id === id ? held.current.events : [];
    const last = have.at(-1)?.seq ?? 0;
    request<KraftEvent[]>(`/work-items/${encodeURIComponent(id)}/events?after_seq=${last}`).then((r) => {
      if (!live) return;
      // A refused read leaves what is held; with nothing held yet, it reads as no events.
      if (r.status !== 200 || !Array.isArray(r.body)) return void (held.current?.id !== id && setLog({ id, events: [] }));
      const more = r.body.filter((e) => e.seq > last);
      if (held.current?.id === id && !more.length) return;
      held.current = { id, events: [...have, ...more] };
      setLog(held.current);
    });
    return () => { live = false; };
  }, [id, version, on]);
  return log?.id === id ? log.events : null;
}
