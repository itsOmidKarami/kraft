import { appliedAt, type Applied } from "./applied";

const fieldText = (a: Applied) => [...Object.entries(a.task_config ?? {}), ...Object.entries(a.policy ?? {}).map(([k, v]) => [`policy.${k}`, v] as const)].map(([k, v]) => `${k} ${Array.isArray(v) ? v.join(", ") : String(v)}`);

/** One line per path (Decided 11). They have no reset: the draft can only set. */
export const appliedRows = (applied: Record<string, Applied> | undefined, path?: string) =>
  (path === undefined ? Object.entries(applied ?? {}) : appliedAt(applied ?? {}, path)).map(([p, a]) => ({ path: p, text: fieldText(a).join(", ") }));

/** "Changed for this item" for a node, step or task pane. */
export function AppliedRows({ applied, path }: { applied?: Record<string, Applied>; path: string }) {
  const rows = appliedRows(applied, path);
  if (!rows.length) return null;
  return (
    <>
      <h3 className="ip-h">Changed for this item</h3>
      <ul className="ip-overrides">
        {rows.map((r) => <li key={r.path}><span className="is-mono">{r.path}</span> {r.text} <span className="item-muted">applied by the draft</span></li>)}
      </ul>
    </>
  );
}
