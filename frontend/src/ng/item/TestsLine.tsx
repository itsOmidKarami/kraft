import type { ItemDetail } from "./useItem";

/** "✓ 3 scopes", or "✗ 1 of 3 scopes" with each red scope linked to its session's log. The desktop's gate pane and the
 *  phone's gate node page both read it. `linkClass` is the screen's own link style. */
export function TestsLine({ result, linkClass = "item-link is-mono" }: { result: NonNullable<ItemDetail["test_result"]>; linkClass?: string }) {
  const n = result.scopes.length;
  if (result.passed) return <>✓ {n} {n === 1 ? "scope" : "scopes"}</>;
  const red = result.scopes.filter((s) => !s.passed);
  return (
    <>
      ✗ {red.length} of {n} {n === 1 ? "scope" : "scopes"}
      {red.map((s) => <span key={s.session_id}> · <a className={linkClass} href={`/api/worker-sessions/${encodeURIComponent(s.session_id)}/log`} target="_blank" rel="noreferrer">{s.scope ?? s.command}</a></span>)}
    </>
  );
}
