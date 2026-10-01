import type { ReactNode, RefObject } from "react";
import { Popover } from "../../ui/Popover";
import { IdRow } from "./IdRow";

/** A small card on a name: the id field with the text selected (Decisions §9
 *  First step, Rename). Enter keeps or renames it. */
export function IdCard({ anchor, title, initial, taken, go, refused, note, onGo, onClose }: { anchor: RefObject<HTMLElement | null>; title: string; initial: string; taken: string[]; go: string; refused?: string | null; note?: ReactNode; onGo: (id: string) => void; onClose: () => void }) {
  return (
    <Popover anchor={anchor} open onClose={onClose} role="dialog" label={title}>
      <div className="seam-id">
        <p className="menu-title">{title}</p>
        <IdRow label={title} initial={initial} taken={taken} go={go} refused={refused} onGo={onGo} />
        {note && <div className="menu-note">{note}</div>}
      </div>
    </Popover>
  );
}
