import type { StopLimit, WorkItem } from "../../types";

/** The PATCH body that sets one limit in the item's own policy override:
 *  item-wide, or on the fix loop's node under `paths`. A `PATCH policy`
 *  replaces the whole override, so the body carries the rest of it along:
 *  every other item-wide field, every other path, and the path's other fields. */
export function limitPolicy(override: WorkItem["policy_override"], limit: Pick<StopLimit, "path" | "key">, n: number) {
  const { paths, ...wide } = override ?? {};
  if (!limit.path) return { policy: { ...wide, ...(paths ? { paths } : {}), [limit.key]: n } };
  return { policy: { ...wide, paths: { ...paths, [limit.path]: { ...paths?.[limit.path], [limit.key]: n } } } };
}
