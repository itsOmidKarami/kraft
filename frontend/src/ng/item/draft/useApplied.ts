import { useEffect, useState } from "react";
import type { KraftEvent } from "../../../types";
import { request } from "../../http";
import { appliedOverrides, type Applied } from "./applied";

/** What applied drafts set on this item, folded from its whole event log: the
 *  API has no event-type filter, and the page's own read holds only the last 100
 *  (a draft applied long ago would drop out). Read when `on` (a Config tab is
 *  open) and again when the item changes. */
export function useApplied(id: string, version: string, on: boolean): Record<string, Applied> {
  const [applied, setApplied] = useState<Record<string, Applied>>({});
  useEffect(() => {
    if (!on) return;
    let live = true;
    void request<KraftEvent[]>(`/work-items/${encodeURIComponent(id)}/events`).then((r) => {
      if (live && r.status === 200 && Array.isArray(r.body)) setApplied(appliedOverrides(r.body));
    });
    return () => { live = false; };
  }, [id, version, on]);
  return applied;
}
