import { createElement, useEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { RotateCcw } from "lucide-react";
import { useIconSet } from "../iconSet";
import { Popover } from "../ui/Popover";
import { focusSoon } from "./menus/focus";

const COLS = 8;
/** Enough to scan; a search narrows the rest. */
const SHOWN = 160;

/** The icon picker (Decisions §9 Icons): a search over the whole Lucide set,
 *  a grid, and "↺ Default" for the kind's icon. Arrows move in the grid,
 *  Enter picks, Escape closes back to the button. */
export function IconPicker({ anchor, current, onPick, onClose }: { anchor: RefObject<HTMLElement | null>; current?: string; onPick: (name: string | null) => void; onClose: () => void }) {
  const set = useIconSet(true);
  const [q, setQ] = useState("");
  const search = useRef<HTMLInputElement>(null);
  const cells = useRef<(HTMLButtonElement | null)[]>([]);
  useEffect(() => void focusSoon(search.current), []);
  const names = set ? Object.keys(set).filter((n) => !q || n.includes(q.trim().toLowerCase())) : [];
  const shown = names.slice(0, SHOWN);
  const move = (e: KeyboardEvent, i: number) => {
    const d = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: COLS, ArrowUp: -COLS }[e.key];
    if (d === undefined) return;
    e.preventDefault();
    const to = i + d;
    if (to < 0) search.current?.focus();
    else cells.current[Math.min(to, shown.length - 1)]?.focus();
  };
  return (
    <Popover anchor={anchor} open onClose={onClose} role="dialog" label="Choose an icon">
      <div className="ip">
        <div className="ip-head">
          <input ref={search} className="picklist-q" aria-label="Search icons" placeholder="Search icons" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); cells.current[0]?.focus(); }
            if (e.key === "Enter" && shown[0]) onPick(shown[0]);
          }} />
          <button type="button" className="menu-item ip-default" onClick={() => onPick(null)}><RotateCcw size={12} aria-hidden /> Default</button>
        </div>
        {!set ? <p className="picklist-empty">Loading icons…</p> : (
          <>
            <div className="ip-grid" role="listbox" aria-label="Icons">
              {shown.map((n, i) => (
                <button key={n} ref={(el) => void (cells.current[i] = el)} type="button" role="option" aria-selected={n === current} aria-label={n} title={n} className={`ip-cell${n === current ? " is-on" : ""}`} onKeyDown={(e) => move(e, i)} onClick={() => onPick(n)}>
                  {createElement(set[n], { size: 16, "aria-hidden": true })}
                </button>
              ))}
            </div>
            <p className="picklist-empty">{names.length > SHOWN ? `${SHOWN} of ${names.length} · search to narrow` : names.length ? `${names.length} icon${names.length === 1 ? "" : "s"}` : "No icon matches."}</p>
          </>
        )}
      </div>
    </Popover>
  );
}
