import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Popover } from "./Popover";
import "./ui.css";

export interface MenuItem {
  label: string;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
}

/** A button that opens a list of actions. Focus moves into the list; ↑/↓,
 *  Home and End move it; Escape, or picking an item, closes the list and
 *  hands focus back to the button. */
export function Menu({ label, trigger, items }: { label: string; trigger: ReactNode; items: MenuItem[] }) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const close = useCallback(() => {
    setOpen(false);
    button.current?.focus();
  }, []);
  const live = items.flatMap((it, i) => (it.disabled ? [] : [i]));

  useEffect(() => {
    if (open) refs.current[live[0]]?.focus();
    // Only on opening: the list is new then.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const onKey = (e: KeyboardEvent) => {
    const at = live.indexOf(refs.current.findIndex((r) => r === document.activeElement));
    const n = live.length;
    const next = { ArrowDown: (at + 1) % n, ArrowUp: (at - 1 + n) % n, Home: 0, End: n - 1 }[e.key];
    if (next === undefined || !n) return;
    e.preventDefault();
    refs.current[live[next]]?.focus();
  };

  return (
    <>
      <button ref={button} type="button" className="icon-btn" aria-label={label} title={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {trigger}
      </button>
      <Popover anchor={button} open={open} onClose={close} role="menu" label={label}>
        <div onKeyDown={onKey} className="menu">
          {items.map((it, i) => (
            <button
              key={it.label}
              ref={(el) => void (refs.current[i] = el)}
              type="button"
              role="menuitem"
              tabIndex={-1}
              disabled={it.disabled}
              className={it.danger ? "menu-item menu-item-danger" : "menu-item"}
              onClick={() => {
                close();
                it.onSelect();
              }}
            >
              {it.label}
            </button>
          ))}
        </div>
      </Popover>
    </>
  );
}
