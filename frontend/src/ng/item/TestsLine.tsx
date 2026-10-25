import { logTextUrl } from "../../api";
import { linkButtonClass } from "../ui/LinkButton";
import type { ItemDetail } from "./useItem";

type Result = NonNullable<ItemDetail["test_result"]>;
/** Whether the scopes that ran did so on their targeted command: all, some or none of them, as a word to put before
 *  "tests passing". A pass that ran only a targeted command is never read as a full one. */
export function targetedWords(result: Result): "" | "targeted " | "partly targeted " {
  const n = result.scopes.filter((s) => s.targeted).length;
  if (!n) return "";
  return n === result.scopes.length ? "targeted " : "partly targeted ";
}

/** "✓ 3 scopes", or "✗ 1 of 3 scopes" with each red scope linked to its session's log. A pass says how many of its
 *  scopes ran targeted: "✓ 3 scopes (targeted)", "(1 targeted)". The desktop's gate pane and the
 *  phone's gate node page both read it. `linkClass` is the screen's own link style. */
export function TestsLine({ workItemId, result, linkClass = linkButtonClass({ mono: true }) }: { workItemId: string; result: Result; linkClass?: string }) {
  const n = result.scopes.length;
  if (result.passed) {
    const targeted = result.scopes.filter((s) => s.targeted).length;
    const note = !targeted ? "" : targeted === n ? " (targeted)" : ` (${targeted} targeted)`;
    return <>✓ {n} {n === 1 ? "scope" : "scopes"}{note}</>;
  }
  const red = result.scopes.filter((s) => !s.passed);
  return (
    <>
      ✗ {red.length} of {n} {n === 1 ? "scope" : "scopes"}
      {red.map((s) => <span key={s.session_id}> · <a className={linkClass} href={logTextUrl(workItemId, s.session_id)} target="_blank" rel="noreferrer">{s.command}</a></span>)}
    </>
  );
}
