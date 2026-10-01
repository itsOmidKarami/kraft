import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

export type Pick = { key: string; label: string; sub?: string; icon?: ReactNode };

/** A searchable list in a popover (the task menu's library, the extend menu):
 *  ↑/↓ move between the search field and the items, Enter picks. */
export function PickList({ items, placeholder, empty, onPick, autoFocus = true }: { items: Pick[]; placeholder: string; empty: string; onPick: (key: string) => void; autoFocus?: boolean }) {
  const [q, setQ] = useState("");
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => { if (autoFocus) search.current?.focus(); }, [autoFocus]);
  const shown = items.filter((i) => !q || `${i.label} ${i.sub ?? ""}`.toLowerCase().includes(q.toLowerCase()));
  const move = (e: KeyboardEvent, from: number) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const to = from + (e.key === "ArrowDown" ? 1 : -1);
    if (to < 0) search.current?.focus();
    else refs.current[Math.min(to, shown.length - 1)]?.focus();
  };
  return (
    <div className="picklist">
      <input ref={search} className="picklist-q" aria-label={placeholder} placeholder={placeholder} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => {
        move(e, -1);
        if (e.key === "Enter" && shown[0]) { e.preventDefault(); onPick(shown[0].key); }
      }} />
      <div className="picklist-items" role="listbox" aria-label={placeholder}>
        {shown.map((i, k) => (
          <button key={i.key} ref={(el) => void (refs.current[k] = el)} type="button" role="option" aria-selected={false} className="menu-item picklist-item" onKeyDown={(e) => move(e, k)} onClick={() => onPick(i.key)}>
            {i.icon}
            <span><span className="picklist-name">{i.label}</span>{i.sub && <span className="picklist-sub">{i.sub}</span>}</span>
          </button>
        ))}
        {!shown.length && <p className="picklist-empty">{empty}</p>}
      </div>
    </div>
  );
}
