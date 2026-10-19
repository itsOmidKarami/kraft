import { useId, type ReactNode, type RefObject } from "react";
import { Check, X } from "lucide-react";
import { Button } from "../../ui/Button";
import { Popover } from "../../ui/Popover";
import { mod } from "../../keys";

export type BaseCheck = { kept: unknown[]; dropped: { key: string; why?: string }[] };
const label = (x: unknown) => (typeof x === "string" ? x : String((x as { key?: string; label?: string }).label ?? (x as { key?: string }).key ?? JSON.stringify(x)));

/** One kept or dropped entry: a chain's edit (`remove spec`) reads as the edit, then what it names; a node's key is the key. The why, if any, under it. */
function Entry({ entry, why, kept }: { entry: string; why?: string; kept: boolean }) {
  const [verb, ...rest] = entry.split(" ");
  const Mark = kept ? Check : X;
  return (
    <li className="cb-entry">
      <Mark className={`cb-mark ${kept ? "is-ok" : "is-bad"}`} size={13} aria-hidden="true" />
      <span className="cb-text">
        <span>{rest.length ? <>{verb} <code>{rest.join(" ")}</code></> : <code>{entry}</code>}</span>
        {why && <span className="cb-why">{why}</span>}
      </span>
    </li>
  );
}

/** "Keeps" or "Drops": a heading over its list, which it names. */
function Group({ head, children }: { head: string; children: ReactNode }) {
  const id = useId();
  return (
    <>
      <p id={id} className="cb-head">{head}</p>
      <ul className="cb-list" aria-labelledby={id}>{children}</ul>
    </>
  );
}

/** Change base (Decisions §9 Inherited items): what the new base keeps of
 *  this chain's overrides and what it drops, with why, before it applies. */
export function ChangeBaseCard({ anchor, node, base, check, empty = "This node has no overrides to carry over.", onApply, onClose }: { anchor: RefObject<HTMLElement | null>; node: string; base: string; check: BaseCheck; /** What it says when there is nothing to keep or drop. */ empty?: string; onApply: () => void; onClose: () => void }) {
  const none = !check.kept.length && !check.dropped.length;
  return (
    <Popover anchor={anchor} open onClose={onClose} role="dialog" label={`Change base of ${node}`}>
      <div className="seam-id card">
        <p className="menu-title">Change base of {node} to {base}?</p>
        {none && <p className="tpl-menu-note">{empty}</p>}
        {/* Only the marks carry colour; the words say which list is which. */}
        {check.kept.length > 0 && <Group head="Keeps">{check.kept.map((k, i) => <Entry key={i} entry={label(k)} kept />)}</Group>}
        {check.dropped.length > 0 && <Group head="Drops">{check.dropped.map((k, i) => <Entry key={i} entry={k.key} why={k.why} kept={false} />)}</Group>}
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
