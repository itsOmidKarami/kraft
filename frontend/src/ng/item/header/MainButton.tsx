import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Archive, ArchiveRestore, ChevronDown, ChevronUp, CircleAlert, CircleCheck, MessageSquare, Pause, Play, RotateCcw, Siren, X } from "../../icons";
import { Popover } from "../../ui/Popover";
import { MAIN_LABEL, type Main, type PanelItem } from "../status";
import { tip } from "../../ui/Tooltip";

const MAIN_ICON: Record<Main, typeof Pause> = { pause: Pause, resume: Play, start: Play, raise: Play, retry: RotateCcw, archive: Archive, restore: ArchiveRestore, gate: CircleCheck, answer: MessageSquare, conflicts: CircleAlert, reopen: RotateCcw };
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

/** The main button (Decisions §1, §14, prototype): one bordered button, the
 *  label and a ▾. The label is the action (a click, Enter or Space). With a
 *  panel, hovering the button or pressing ▾ opens a menu over it at its width:
 *  the main action first (▴), the panel's items under it. ↓, Enter or Space on
 *  ▾ open it from the keyboard, on the main action; Escape closes it and focus
 *  goes back to ▾, so nothing here is reachable by hover alone. */
export function MainButton({ main, panel: all, archivable, busy, onMain, onItem, groupRef, children }: Props) {
  // The main action is the menu's first row; a panel item that is the same action would repeat it.
  const panel = all.filter((it) => it !== main);
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const leave = useRef<ReturnType<typeof setTimeout> | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const focusFirst = useRef(false);
  useEffect(() => {
    // A frame later: the popover is hidden until it has measured its place, and a hidden element takes no focus.
    if (open && focusFirst.current) requestAnimationFrame(() => refs.current[0]?.focus());
    focusFirst.current = false;
  }, [open]);
  const close = useCallback(() => setOpen(false), []);
  const Icon = MAIN_ICON[main];
  const menu = panel.length > 0;
  const act = () => !busy && onMain();

  const onToggleKey = (e: KeyboardEvent) => {
    if (!(e.key === "ArrowDown" || e.key === "Enter" || e.key === " ")) return;
    e.preventDefault();
    focusFirst.current = true;
    setOpen(true);
  };
  // A toast that shows up over the button (they sit top-right, as the header
  // does) is part of the hover area: the close waits until the pointer leaves it too.
  // A toast that goes (its timeout, not the pointer) counts as left: a still
  // pointer gets no mouseleave from an element that shrinks away under it.
  const onToast = useRef<{ el: Element; off: () => void; gone: MutationObserver } | null>(null);
  const unwatch = () => {
    onToast.current?.el.removeEventListener("mouseleave", onToast.current.off);
    onToast.current?.gone.disconnect();
    onToast.current = null;
  };
  // Set by a press that closed the menu to do something: the pointer then lands on the button, where
  // the menu's own row was, and its mouseenter must not open the menu again over the dialog that
  // press opened (R13b-03). Cleared when the pointer leaves.
  const quiet = useRef(false);
  const hover = (on: boolean) => {
    if (!menu) return;
    if (on && quiet.current) return;
    if (leave.current) clearTimeout(leave.current);
    unwatch();
    if (on) setOpen(true);
    else leave.current = setTimeout(close, 160);
  };
  const left = (e: React.MouseEvent) => {
    quiet.current = false;
    const toasts = e.relatedTarget instanceof Element ? e.relatedTarget.closest(".toasts") : null;
    if (!menu || !toasts) return hover(false);
    if (leave.current) clearTimeout(leave.current);
    unwatch();
    const off = () => hover(false);
    const gone = new MutationObserver((changes) => changes.some((c) => c.removedNodes.length) && off());
    toasts.addEventListener("mouseleave", off);
    gone.observe(toasts, { childList: true });
    onToast.current = { el: toasts, off, gone };
  };
  useEffect(() => unwatch, []);

  return (
    <div className="item-main" ref={groupRef} onMouseEnter={() => hover(true)} onMouseLeave={left}>
      {/* aria-disabled, not disabled, while busy: a browser takes focus off a button it
          disables, and the focus of Start, Apply and start or Resume fell to the page (R10b-04). */}
      <button ref={button} type="button" className={`item-main-action is-${main}`} aria-disabled={busy || undefined} onClick={() => { quiet.current = true; close(); act(); }}>
        <Icon size={13} aria-hidden />
        {/* Every row's label in one cell, only the main one shown: the button is as wide as its widest row, so the menu over it fits at its width. */}
        <span className="item-main-label">
          <span>{MAIN_LABEL[main]}</span>
          {panel.map((it) => <span key={it} className="item-main-sizer" aria-hidden>{ITEM[it].label}</span>)}
        </span>
      </button>
      {menu && (
        <button ref={toggle} type="button" className="item-main-toggle" {...tip("More actions")} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(true)} onKeyDown={onToggleKey}>
          <ChevronDown size={12} aria-hidden />
        </button>
      )}
      <Popover anchor={groupRef} over open={open && menu} onClose={() => {
        close();
        if (list.current?.contains(document.activeElement)) toggle.current?.focus();
      }} role="menu" label="Item actions" focusIn={false}>
        <div ref={list} className="menu item-panel" onMouseEnter={() => hover(true)} onMouseLeave={left}>
          <button ref={(el) => void (refs.current[0] = el)} type="button" role="menuitem" tabIndex={-1} aria-disabled={busy || undefined} className={`menu-item item-panel-item item-panel-main is-${main}`} onClick={() => { quiet.current = true; close(); button.current?.focus(); act(); }}>
            <Icon size={13} aria-hidden /> <span className="item-main-label">{MAIN_LABEL[main]}</span>
            {/* Over ▾: a press there, meant to open the menu, must not run the action. */}
            <span className="item-panel-caret" aria-hidden onClick={(e) => e.stopPropagation()}><ChevronUp size={12} /></span>
          </button>
          {panel.map((it, i) => {
            const { label, icon: I, tone } = ITEM[it];
            return (
              <button
                key={it}
                ref={(el) => void (refs.current[i + 1] = el)}
                type="button"
                role="menuitem"
                tabIndex={-1}
                disabled={it === "archive" && !archivable}
                className={`menu-item item-panel-item${tone ? ` is-${tone}` : ""}`}
                onClick={() => { close(); toggle.current?.focus(); onItem(it); }}
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
