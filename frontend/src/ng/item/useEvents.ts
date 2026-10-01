import { useEffect, useState } from "react";
import type { KraftEvent } from "../../types";
import { request } from "../http";

/** The newest events of an item, paged backwards (B13), read again when the
 *  item changes: the chain pane's Recent and who passed each gate. */
export const RECENT = 100;
export function useEvents(id: string, version: string): KraftEvent[] {
  const [events, setEvents] = useState<KraftEvent[]>([]);
  useEffect(() => {
    let live = true;
    // before_seq past any real seq: the last RECENT events, oldest first.
    request<KraftEvent[]>(`/work-items/${encodeURIComponent(id)}/events?before_seq=${2 ** 31 - 1}&limit=${RECENT}`).then((r) => {
      if (live && r.status === 200 && Array.isArray(r.body)) setEvents(r.body);
    });
    return () => { live = false; };
  }, [id, version]);
  return events;
}
