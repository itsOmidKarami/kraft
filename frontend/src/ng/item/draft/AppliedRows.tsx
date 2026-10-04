import { appliedRows, type Applied } from "./applied";

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
