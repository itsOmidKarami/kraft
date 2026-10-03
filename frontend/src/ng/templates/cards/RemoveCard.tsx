import { useEffect, useRef, type RefObject } from "react";
import { Button } from "../../ui/Button";
import { Popover } from "../../ui/Popover";
import { focusSoon } from "../menus/focus";
import type { Ref } from "./refs";
import { mod } from "../../keys";

/** Remove (Decisions §9 Remove): a small card at the button. What points at
 *  the item is listed (and marked red on the canvas while the card is open);
 *  removing leaves those as problems, never re-targeted. */
export function RemoveCard({ anchor, label, refs, note, onRemove, onClose }: { anchor: RefObject<HTMLElement | null>; label: string; refs: Ref[]; note?: string; onRemove: () => void; onClose: () => void }) {
  // Focus lands on Keep: a destructive action is never the default.
  const keep = useRef<HTMLButtonElement>(null);
  useEffect(() => void focusSoon(keep.current), []);
  return (
    <Popover anchor={anchor} open onClose={onClose} role="dialog" label={label}>
      <div className="seam-id card">
        <p className="menu-title">{label}?</p>
        {refs.length > 0 && (
          <>
            <p className="tpl-menu-note">These point at it and become problems that block publishing; Kraft won't re-target them:</p>
            <ul className="card-refs is-bad">{refs.map((r) => <li key={r.path}>{r.path}</li>)}</ul>
          </>
        )}
        {note && <p className="tpl-menu-note">{note}</p>}
        <p className="tpl-menu-note">{mod("Z")} undoes it.</p>
        <div className="card-acts">
          <button ref={keep} type="button" className="btn btn-secondary" onClick={onClose}>Keep</button>
          <Button variant="danger" onClick={onRemove}>{label}</Button>
        </div>
      </div>
    </Popover>
  );
}
