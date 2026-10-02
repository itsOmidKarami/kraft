import { useMemo } from "react";
import { useEventLog } from "../useEvents";
import { appliedOverrides, type Applied } from "./applied";

/** What applied drafts set on this item, folded from its whole event log: the
 *  API has no event-type filter, and the page's own read holds only the last 100
 *  (a draft applied long ago would drop out). Read when `on` (a Config tab is
 *  open) and again when the item changes, only what came after the last read. */
export function useApplied(id: string, version: string, on: boolean): Record<string, Applied> {
  const events = useEventLog(id, version, on);
  return useMemo(() => (events ? appliedOverrides(events) : {}), [events]);
}
