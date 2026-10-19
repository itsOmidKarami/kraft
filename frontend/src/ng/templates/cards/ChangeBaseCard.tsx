import type { RefObject } from "react";
import { Button } from "../../ui/Button";
import { Popover } from "../../ui/Popover";
import { mod } from "../../keys";

export type BaseCheck = { kept: unknown[]; dropped: { key: string; why?: string }[] };
const label = (x: unknown) => (typeof x === "string" ? x : String((x as { key?: string; label?: string }).label ?? (x as { key?: string }).key ?? JSON.stringify(x)));

/** Change base (Decisions §9 Inherited items): what the new base keeps of
 *  this chain's overrides and what it drops, with why, before it applies. */
export function ChangeBaseCard({ anchor, node, base, check, empty = "This node has no overrides to carry over.", onApply, onClose }: { anchor: RefObject<HTMLElement | null>; node: string; base: string; check: BaseCheck; /** What it says when there is nothing to keep or drop. */ empty?: string; onApply: () => void; onClose: () => void }) {
  const none = !check.kept.length && !check.dropped.length;
  return (
    <Popover anchor={anchor} open onClose={onClose} role="dialog" label={`Change base of ${node}`}>
      <div className="seam-id card">
        <p className="menu-title">Change base of {node} to {base}?</p>
        {none && <p className="tpl-menu-note">{empty}</p>}
        {check.kept.length > 0 && (<><p className="tpl-menu-note">Kept:</p><ul className="card-refs">{check.kept.map((k, i) => <li key={i}>{label(k)}</li>)}</ul></>)}
        {check.dropped.length > 0 && (<><p className="tpl-menu-note">Dropped:</p><ul className="card-refs is-bad">{check.dropped.map((k, i) => <li key={i}>{k.key}{k.why ? ` · ${k.why}` : ""}</li>)}</ul></>)}
        <p className="tpl-menu-note">{mod("Z")} undoes it.</p>
        <div className="card-acts">
          <Button onClick={onClose}>Cancel</Button>
          {/* What it drops is work lost, so the button says so, as Remove's does. */}
          <Button variant={check.dropped.length ? "danger" : "primary"} onClick={onApply}>Change base</Button>
        </div>
      </div>
    </Popover>
  );
}
