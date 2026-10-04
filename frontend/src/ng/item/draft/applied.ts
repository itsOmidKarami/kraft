import type { KraftEvent } from "../../../types";
import type { OverrideOp } from "./types";

export type Applied = { task_config?: Record<string, unknown>; policy?: Record<string, unknown> };

/** What applied drafts set, per path, folded from the item's `chain_revised`
 *  events with `source: "draft"` (Decided 11). Events come oldest first; a later
 *  one wins per path and field. The item API holds no other record of them. */
export function appliedOverrides(events: KraftEvent[]): Record<string, Applied> {
  const out: Record<string, Applied> = {};
  for (const e of events) {
    if (e.type !== "chain_revised" || e.payload.source !== "draft" || !Array.isArray(e.payload.changes)) continue;
    for (const op of e.payload.changes as { op?: string }[]) {
      if (op.op !== "override") continue;
      const o = op as OverrideOp;
      const at = (out[o.path] ??= {});
      if (o.task_config) at.task_config = { ...at.task_config, ...o.task_config };
      if (o.policy) at.policy = { ...at.policy, ...o.policy };
    }
  }
  return out;
}

/** The paths at or under `path` that an applied draft set (a node's rows, a task's row). */
export const appliedAt = (all: Record<string, Applied>, path: string): [string, Applied][] =>
  Object.entries(all).filter(([p]) => p === path || p.startsWith(`${path}.`));

const fieldText = (a: Applied) => [...Object.entries(a.task_config ?? {}), ...Object.entries(a.policy ?? {}).map(([k, v]) => [`policy.${k}`, v] as const)].map(([k, v]) => `${k} ${Array.isArray(v) ? v.join(", ") : String(v)}`);

/** One line per path (Decided 11). They have no reset: the draft can only set. */
export const appliedRows = (applied: Record<string, Applied> | undefined, path?: string) =>
  (path === undefined ? Object.entries(applied ?? {}) : appliedAt(applied ?? {}, path)).map(([p, a]) => ({ path: p, text: fieldText(a).join(", ") }));
