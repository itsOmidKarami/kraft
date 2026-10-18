import type { KeyboardEvent, MouseEvent } from "react";
import { ago, repoName, shortId } from "../../format";
import type { WorkItem } from "../../types";
import { NodeGlyph } from "../graph/NodeGlyph";
import { breakable } from "../phone/ui/breakable";
import { CUE_ICON } from "./cue";
import { chainOf, groupOf } from "./model";
import { actionVariant, admittedRowAction, glyphOf, reasonTail, stopCueOf, ticksOf, type RowAction } from "./rowText";
import { IN_YOUR_CHECKOUT } from "./sharedWords";
import { Ticks } from "./Ticks";

export interface RowProps {
  item: WorkItem;
  selected: boolean;
  checked: boolean;
  offline: boolean;
  now: number;
  /** An action's error, shown on the row (B.7). */
  error?: string | null;
  onSelect: (id: string) => void;
  onOpen: (id: string) => void;
  onCheck: (id: string) => void;
  onAction: (item: WorkItem, action: RowAction) => void;
  /** On the board's list, which is one Tab stop: set, this row is the stop or is not, and its box and action leave the tab order (the list's keys reach them). Unset, every control tabs. */
  stop?: boolean;
  /** The meta line leaves out what the view already says: the one repo in view, the chain a group is. */
  hideRepo?: boolean;
  hideChain?: boolean;
  /** The archived view's own action and tail (H): Restore, "archived by you". */
  own?: { label: string; run: () => void; tail: string; age: string };
}

/** One board row (AreaBoard 77–86): checkbox, glyph, title and meta, ticks,
 *  one action. The title area is the row's button; the list roves ↑/↓ across them. */
export function Row({ item, selected, checked, offline, now, error, onSelect, onOpen, onCheck, onAction, own, stop, hideRepo, hideChain }: RowProps) {
  const g = glyphOf(item);
  const ticks = ticksOf(item);
  const roved = stop !== undefined;
  const off = roved ? -1 : undefined;
  const act = own ? null : admittedRowAction(item);
  // The tail clips on a narrow row (a queue line loses "is using it"): its title keeps the whole of it.
  const tail = own?.tail ?? reasonTail(item, now);
  // A tail that leads with "the checkout" (a checkout wait, most checkout stops) already says in place: a narrow row gives
  // the marker's room to it. Only its lead: the 3-line clamp may hide a later mention behind a long folder.
  const saysCheckout = !own && /^(?:waiting for )?the checkout\b/i.test(tail);
  const id = item.bead_id || shortId(item.id);
  const hot = groupOf(item) === "needs";
  const cue = hot ? stopCueOf(item) : null;
  const Cue = cue ? CUE_ICON[cue] : null;
  // A row's action is named by what it does ("Review") and described by its item:
  // five alike in a row read the same to a screen reader without it (R8b-11).
  const titleId = `board-title-${item.id}`;
  const click = (e: MouseEvent) => (e.metaKey || e.ctrlKey ? onOpen(item.id) : onSelect(item.id));
  const key = (e: KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      onOpen(item.id);
    } else if (e.key === " ") {
      e.preventDefault();
      onCheck(item.id);
    }
  };
  return (
    <div className={`board-row${selected ? " is-sel" : checked ? " is-checked" : ""}${offline ? " is-off" : ""}`} data-row={item.id}>
      <input type="checkbox" className="board-check" checked={checked} onChange={() => onCheck(item.id)} aria-label={`Select ${item.title}`} tabIndex={off} />
      <NodeGlyph kind={g.kind} size="sm" state={g.state} icon={g.icon} sel={selected} />
      <button type="button" className="board-row-main" tabIndex={roved ? (stop ? 0 : -1) : undefined} aria-current={selected || undefined} onClick={click} onDoubleClick={() => onOpen(item.id)} onKeyDown={key} onKeyUp={(e) => e.key === " " && e.preventDefault()}>
        <span className="board-title" id={titleId} title={item.title}>{item.title}</span>
        <span className="board-meta">
          <span className={`board-meta-tail${hot && cue !== "paused" ? " is-hot" : ""}${cue ? ` is-${cue}` : ""}`}>
            {Cue && <Cue size={12} aria-hidden className="board-cue" />}
            <span className="board-tail-text" title={tail}>{breakable(tail)}</span>
          </span>
          {!hideRepo && <><span className="board-sep" aria-hidden>·</span><span className="board-meta-repo" title={item.repo}>{repoName(item.repo)}</span></>}
          {item.in_place && <><span className={`board-sep${saysCheckout ? " is-said" : ""}`} aria-hidden>·</span><span className={`board-meta-inplace${saysCheckout ? " is-said" : ""}`} title={item.repo}>{IN_YOUR_CHECKOUT}</span></>}
          <span className="board-sep" aria-hidden>·</span>
          <span className="board-meta-id">{id}</span>
          {!hideChain && <><span className="board-sep" aria-hidden>·</span><span className="board-meta-chain">{chainOf(item)}</span></>}
          <span className="board-sep" aria-hidden>·</span>
          <span className="board-meta-age">{own?.age ?? ago(item.updated_at, now)}</span>
          {ticks.length > 0 && <span className="board-visually-hidden">, {ticks.filter((t) => t.state === "done").length} of {ticks.length} nodes done</span>}
        </span>
      </button>
      <Ticks ticks={ticks} />
      <span className="board-act">
        {own && <button type="button" className="btn btn-secondary board-act-btn" aria-describedby={titleId} disabled={offline} tabIndex={off} onClick={own.run}>{own.label}</button>}
        {act && (
          <button type="button" className={`btn btn-${actionVariant(act)} board-act-btn`} aria-describedby={titleId} disabled={offline} tabIndex={off} onClick={() => onAction(item, act)}>
            {act.label}
          </button>
        )}
      </span>
      {error && <p className="board-row-error" role="alert">{error}</p>}
    </div>
  );
}
