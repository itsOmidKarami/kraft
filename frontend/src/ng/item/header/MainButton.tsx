import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Archive, ArchiveRestore, ChevronDown, CircleCheck, Pause, Play, RotateCcw, Siren, X } from "../../icons";
import { Popover } from "../../ui/Popover";
import { MAIN_LABEL, type Main, type PanelItem } from "../status";

const MAIN_ICON: Record<Main, typeof Pause> = { pause: Pause, resume: Play, raise: Play, retry: RotateCcw, archive: Archive, restore: ArchiveRestore };
const ITEM: Record<PanelItem, { label: string; icon: typeof Pause; tone?: string }> = {
  escalate: { label: "Escalate…", icon: Siren, tone: "warn" },
  complete: { label: "Mark complete…", icon: CircleCheck },
  archive: { label: "Archive", icon: Archive },
  cancel: { label: "Cancel…", icon: X, tone: "bad" },
};

type Props = {
  main: Main;
  panel: PanelItem[];
  archivable: boolean;
  busy?: boolean;
  onMain: () => void;
  onItem: (it: PanelItem) => void;
  /** The group, for popovers anchored under the whole button (the pause confirm, the cancel card). */
  groupRef: React.RefObject<HTMLDivElement>;
  children?: ReactNode;
};

/** The main button and its panel (Decisions §1, §14): the action, and a ▾
 *  toggle whose panel opens on hover of the group, on the toggle's focus, and
 *  on click, Enter, Space or ↓ there. Escape closes it and focus stays on the
 *  toggle, so nothing here is reachable by hover alone. */
export function MainButton({ main, panel, archivable, busy, onMain, onItem, groupRef, children }: Props) {
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const leave = useRef<ReturnType<typeof setTimeout> | null>(null);
  const list = useRef<HTMLDivElement>(null);
  // Focus handed back to the toggle on Escape must not reopen the panel.
  const quiet = useRef(false);
  const focusFirst = useRef(false);
  useEffect(() => {
    // A frame later: the popover is hidden until it has measured its place, and a hidden element takes no focus.
    if (open && focusFirst.current) requestAnimationFrame(() => refs.current[live[0]]?.focus());
    focusFirst.current = false;
    // Only when the panel opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const live = panel.flatMap((it, i) => (it === "archive" && !archivable ? [] : [i]));
  const close = useCallback(() => setOpen(false), []);
  const Icon = MAIN_ICON[main];

  const onToggleKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (open) refs.current[live[0]]?.focus();
      else {
        focusFirst.current = true;
        setOpen(true);
      }
    }
  };
  const onListKey = (e: KeyboardEvent) => {
    const at = live.indexOf(refs.current.findIndex((r) => r === document.activeElement));
    const n = live.length;
    const next = { ArrowDown: (at + 1) % n, ArrowUp: (at - 1 + n) % n, Home: 0, End: n - 1 }[e.key];
    if (next === undefined || !n) return;
    e.preventDefault();
    refs.current[live[next]]?.focus();
  };
  const hover = (on: boolean) => {
    if (!panel.length) return;
    if (leave.current) clearTimeout(leave.current);
    if (on) setOpen(true);
    else leave.current = setTimeout(close, 160);
  };

  return (
    <div className="item-main" ref={groupRef} onMouseEnter={() => hover(true)} onMouseLeave={() => hover(false)}>
      <button type="button" className={`item-main-action is-${main}`} onClick={onMain} disabled={busy}>
        <Icon size={13} aria-hidden /> {MAIN_LABEL[main]}
      </button>
      {panel.length > 0 && (
        <button
          ref={toggle}
          type="button"
          className="item-main-toggle"
          aria-label="More actions"
          aria-haspopup="menu"
          aria-expanded={open}
          onFocus={() => (quiet.current ? (quiet.current = false) : setOpen(true))}
          onClick={() => setOpen(true)}
          onKeyDown={onToggleKey}
        >
          <ChevronDown size={12} aria-hidden />
        </button>
      )}
      <Popover anchor={groupRef} open={open && panel.length > 0} onClose={() => {
        close();
        if (list.current?.contains(document.activeElement)) { quiet.current = true; toggle.current?.focus(); }
      }} role="menu" label="Item actions">
        <div ref={list} className="menu item-panel" onKeyDown={onListKey} onMouseEnter={() => hover(true)} onMouseLeave={() => hover(false)} style={{ width: groupRef.current?.offsetWidth }}>
          {panel.map((it, i) => {
            const { label, icon: I, tone } = ITEM[it];
            return (
              <button
                key={it}
                ref={(el) => void (refs.current[i] = el)}
                type="button"
                role="menuitem"
                tabIndex={-1}
                disabled={it === "archive" && !archivable}
                className={`menu-item item-panel-item${tone ? ` is-${tone}` : ""}`}
                onClick={() => { close(); onItem(it); }}
              >
                <I size={13} aria-hidden /> {label}
              </button>
            );
          })}
        </div>
      </Popover>
      {children}
    </div>
  );
}
