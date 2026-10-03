import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Maximize2, NodeIcon, PanelRightClose, PanelRightOpen, type TaskKind } from "../icons";
import { Tabs } from "../ui/Tabs";
import { holdsText, isEscape } from "../keys";
import type { useResizable } from "./useResizable";
import "./graph.css";
import { tip } from "../ui/Tooltip";

type Props = {
  /** The pane's id, for its tabs and their panel. */
  id: string;
  open: boolean;
  /** From useResizable: width, overlay, and the handle's props (none when overlaid). */
  size: ReturnType<typeof useResizable>;
  crumbs: { label: string; onClick?: () => void }[];
  icon?: string;
  taskKind?: TaskKind;
  gate?: boolean;
  title: string;
  /** The title is prose (an item's title), not an id: set in the text face. */
  prose?: boolean;
  sub?: string;
  prob?: { msg: string; fix?: string };
  /** A control above the tabs that every tab reads, such as the item page's attempt switcher. */
  bar?: ReactNode;
  tabs?: { value: string; label: string }[];
  tab?: string;
  onTab?: (t: string) => void;
  onCollapse: () => void;
  onExpand: () => void;
  /** Focus ⤢: opens the node view. */
  onFocus?: () => void;
  /** The editor's icon picker (Decisions §9 Icons): the glyph becomes "icon ▾". */
  onIcon?: (el: HTMLElement) => void;
  /** A click on the title (the editor's rename, Decisions §9 Rename); the title is then a button. */
  onTitle?: () => void;
  /** The title's in-place editor while it is open, in the title's place. */
  titleEdit?: ReactNode;
  footer?: ReactNode;
  children?: ReactNode;
};

/** The side pane over a canvas (Inspector.dc.html): crumb, icon and title,
 *  tabs, a body that alone scrolls, a footer; collapses to a 40px rail. */
export function Inspector({ id, open, size, crumbs, icon, taskKind, gate, title, prose, sub, prob, bar, tabs, tab, onTab, onCollapse, onExpand, onFocus, onTitle, titleEdit, onIcon, footer, children }: Props) {
  const railBtn = useRef<HTMLButtonElement>(null);
  const fromKeys = useRef(false);
  // Escape, or the collapse button, lands focus on the rail that replaces the pane.
  useEffect(() => {
    if (!open && fromKeys.current) railBtn.current?.focus();
    fromKeys.current = false;
  }, [open]);
  // The rename field closing (Enter, Esc, a blank blur) takes the focus with it:
  // it goes back to the title it replaced, not to the page (R9b-09).
  const titleBtn = useRef<HTMLButtonElement>(null);
  const editing = !!titleEdit;
  const wasEditing = useRef(editing);
  useEffect(() => {
    const was = wasEditing.current;
    wasEditing.current = editing;
    if (was && !editing && (!document.activeElement || document.activeElement === document.body)) titleBtn.current?.focus();
  }, [editing]);
  const glyph = gate ? <span className="pane-diamond" aria-hidden="true" /> : <NodeIcon name={icon} kind={taskKind} size={14} />;

  if (!open)
    return (
      <aside className="pane-rail" aria-label={`${title} pane, collapsed`}>
        <button ref={railBtn} type="button" className="rail-btn" {...tip("Expand pane")} onClick={onExpand}><PanelRightOpen size={14} /></button>
        <button type="button" className="rail-name" aria-label={`Expand ${title}`} onClick={onExpand}>
          <span className="rail-icon">{glyph}{prob && <span className="rail-prob" aria-label="has a problem" />}</span>
          <span className="rail-title">{title}</span>
        </button>
      </aside>
    );

  const onKeyDown = (e: KeyboardEvent) => {
    // A dialog or popover opened from the pane is portaled out of it, but React
    // still bubbles its keys here: its Escape is its own, not the pane's.
    if (!isEscape(e) || !e.currentTarget.contains(e.target as Node)) return;
    e.preventDefault();
    e.stopPropagation();
    // Escape in a box with text in it leaves the pane as it is: collapsing it would throw the text away (R11b-02).
    if (holdsText(e.target)) return;
    fromKeys.current = true;
    onCollapse();
  };
  return (
    <aside className={`pane${size.overlay ? " is-overlay" : ""}`} aria-label={`${title} pane`} style={{ width: size.width }} onKeyDown={onKeyDown} onPointerDown={(e) => e.stopPropagation()} onClick={(e) => e.stopPropagation()}>
      {size.handle && <div className="pane-handle" {...size.handle} />}
      <div className="pane-crumb">
        <nav aria-label="Pane path" className="pane-crumbs">
          {crumbs.map((k, i) => (
            <span key={i} className="crumb">
              {k.onClick ? <button type="button" className="crumb-link" onClick={k.onClick}>{k.label}</button> : <span className="crumb-static">{k.label}</span>}
              <span className="crumb-sep" aria-hidden="true">›</span>
            </span>
          ))}
        </nav>
        <button type="button" className="pane-icon-btn" {...tip("Collapse pane", "Collapse pane (Esc)")} onClick={() => { fromKeys.current = true; onCollapse(); }}><PanelRightClose size={14} /></button>
      </div>
      <div className="pane-head">
        <div className="pane-title-row">
          {onIcon ? (
            <button type="button" className="pane-glyph is-picker" {...tip(`Icon${icon ? `: ${icon}` : ""}, change`, "Change the icon")} onClick={(e) => onIcon(e.currentTarget)}>
              {glyph}<ChevronDown size={10} aria-hidden />
            </button>
          ) : <span className="pane-glyph">{glyph}</span>}
          {titleEdit ?? <h2 className={`pane-title${prose ? " is-prose" : ""}`}>{onTitle ? <button ref={titleBtn} type="button" className="pane-title-btn" title="Rename" onClick={onTitle}>{title}</button> : title}</h2>}
          {onFocus && <button type="button" className="pane-focus" title="Open the node view (double-click)" onClick={onFocus}>Focus <Maximize2 size={12} /></button>}
        </div>
        {sub && <p className="pane-sub">{sub}</p>}
      </div>
      {prob && (
        <div className="pane-prob" role="alert">
          <span className="pane-prob-mark" aria-hidden="true">!</span>
          <span><span className="pane-prob-msg">{prob.msg}</span>{prob.fix && <span className="pane-prob-fix">{prob.fix}</span>}</span>
        </div>
      )}
      {bar && <div className="pane-bar">{bar}</div>}
      {tabs && tab && onTab && <div className="pane-tabs"><Tabs id={id} label={`${title} sections`} tabs={tabs} value={tab} onChange={onTab} /></div>}
      <div className="pane-body" {...(tabs && tab ? { role: "tabpanel", id: `${id}-panel`, "aria-labelledby": `${id}-tab-${tab}` } : {})}>{children}</div>
      {footer && <div className="pane-footer">{footer}</div>}
    </aside>
  );
}
