import { useEffect, useRef, type RefObject } from "react";
import { Button } from "../../ui/Button";
import { Popover } from "../../ui/Popover";
import { focusSoon } from "../menus/focus";
import { mod } from "../../keys";

/** Detach: a small card at the header button. The chain stops following its
 *  parent: its nodes are written into its own file (`detach`). */
export function DetachCard({ anchor, chain, parent, onDetach, onClose }: { anchor: RefObject<HTMLElement | null>; chain: string; parent: string; onDetach: () => void; onClose: () => void }) {
  // Focus lands on Keep: a change this large is never the default.
  const keep = useRef<HTMLButtonElement>(null);
  useEffect(() => void focusSoon(keep.current), []);
  return (
    <Popover anchor={anchor} open onClose={onClose} role="dialog" label={`Detach ${chain}`}>
      <div className="seam-id card">
        <p className="menu-title">Detach {chain}?</p>
        <p className="tpl-menu-note">{chain} stops following {parent}. Its nodes are written into the file, and later changes to {parent} no longer reach it.</p>
        <p className="tpl-menu-note">{mod("Z")} undoes it.</p>
        <div className="card-acts">
          <button ref={keep} type="button" className="btn btn-secondary" onClick={onClose}>Keep</button>
          <Button variant="danger" onClick={onDetach}>Detach</Button>
        </div>
      </div>
    </Popover>
  );
}
