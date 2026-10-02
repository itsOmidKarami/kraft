import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Popover } from "./Popover";
import "./ui.css";

export interface MenuItem {
  label: string;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
  /** A pick list (the board's Chain, Group and Sort): ✓ on the current one, read as a radio. */
  checked?: boolean;
  /** Right-aligned and muted, e.g. a count. */
  hint?: string;
  /** A second, quieter line under the label. */
  sub?: string;
  /** A dot after the label: an unpublished draft (amber) or a problem (red); `dotLabel` names it for a screen reader. */
  dot?: "draft" | "problem";
  dotLabel?: string;
  /** A glyph before the label. */
  icon?: ReactNode;
}

/** A button that opens a list of actions. Focus moves into the list; ↑/↓,
 *  Home and End move it (Popover's keys); Escape, or picking an item, closes the list and
 *  hands focus back to the button. With `triggerClass` the trigger is a text
 *  button named by its own text (the list keeps `label`); `note` is a line
 *  under the items, `heading` a small title above them. */
export function Menu({ label, trigger, items, triggerClass, note, heading }: { label: string; trigger: ReactNode; items: MenuItem[]; triggerClass?: string; note?: string; heading?: string }) {
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

  return (
    <>
      <button ref={button} type="button" className={triggerClass ?? "icon-btn"} {...(triggerClass ? {} : { "aria-label": label, title: label })} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {trigger}
      </button>
      <Popover anchor={button} open={open} onClose={close} role="menu" label={label}>
        <div className="menu">
          {heading && <span className="menu-heading" aria-hidden="true">{heading}</span>}
          {items.map((it, i) => (
            <button
              key={it.label}
              ref={(el) => void (refs.current[i] = el)}
              type="button"
              role={it.checked === undefined ? "menuitem" : "menuitemradio"}
              aria-checked={it.checked}
              tabIndex={-1}
              disabled={it.disabled}
              className={it.danger ? "menu-item menu-item-danger" : "menu-item"}
              onClick={() => {
                close();
                it.onSelect();
              }}
            >
              {it.checked !== undefined && <span className="menu-mark" aria-hidden>{it.checked ? "✓" : ""}</span>}
              {it.icon && <span className="menu-icon" aria-hidden>{it.icon}</span>}
              {it.sub ? (
                <span className="menu-text">
                  <span>{it.label}</span>
                  <span className="menu-sub">{it.sub}</span>
                </span>
              ) : (
                it.label
              )}
              {it.dot && <span className={`menu-dot is-${it.dot}`} role="img" aria-label={it.dotLabel ?? (it.dot === "draft" ? "unpublished draft" : "has a problem")} />}
              {it.hint && <span className="menu-hint">{it.hint}</span>}
            </button>
          ))}
          {note && <p className="menu-note">{note}</p>}
        </div>
      </Popover>
    </>
  );
}
