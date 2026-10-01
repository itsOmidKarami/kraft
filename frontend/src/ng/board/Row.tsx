import type { KeyboardEvent, MouseEvent } from "react";
import { ago, repoName, shortId } from "../../format";
import type { WorkItem } from "../../types";
import { NodeGlyph } from "../graph/NodeGlyph";
import { chainOf, groupOf } from "./model";
import { glyphOf, reasonTail, rowAction, ticksOf, type RowAction } from "./rowText";
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
  /** Beside the open peek: no tick strip, so the row's words keep their room. */
  compact?: boolean;
  /** The archived view's own action and tail (H): Restore, "archived by you". */
  own?: { label: string; run: () => void; tail: string; age: string };
}

/** One board row (AreaBoard 77–86): checkbox, glyph, title and meta, ticks,
 *  one action. The title area is the row's button; the list roves ↑/↓ across them. */
export function Row({ item, selected, checked, offline, now, error, onSelect, onOpen, onCheck, onAction, own, compact }: RowProps) {
  const g = glyphOf(item);
  const act = own ? null : rowAction(item);
  const id = item.bead_id || shortId(item.id);
  const hot = groupOf(item) === "needs";
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
    <div className={`board-row${selected ? " is-sel" : checked ? " is-checked" : ""}${offline ? " is-off" : ""}${compact ? " is-compact" : ""}`} data-row={item.id}>
      <input type="checkbox" className="board-check" checked={checked} onChange={() => onCheck(item.id)} aria-label={`Select ${item.title}`} />
      <NodeGlyph kind={g.kind} size="sm" state={g.state} icon={g.icon} sel={selected} />
      <button type="button" className="board-row-main" aria-current={selected || undefined} onClick={click} onDoubleClick={() => onOpen(item.id)} onKeyDown={key} onKeyUp={(e) => e.key === " " && e.preventDefault()}>
        <span className="board-title" title={item.title}>{item.title}</span>
        <span className="board-meta">
          <span className="board-meta-repo" title={item.repo}>{repoName(item.repo)}</span>
          <span className="board-sep" aria-hidden>·</span>
          <span className="board-meta-id">{id}</span>
          <span className="board-sep" aria-hidden>·</span>
          <span className="board-meta-chain">{chainOf(item)}</span>
          <span className="board-sep" aria-hidden>·</span>
          <span className="board-meta-age">{own?.age ?? ago(item.updated_at, now)}</span>
          <span className="board-sep" aria-hidden>·</span>
          <span className={`board-meta-tail${hot ? " is-hot" : ""}`}>{own?.tail ?? reasonTail(item, now)}</span>
        </span>
      </button>
      {!compact && <Ticks ticks={ticksOf(item)} />}
      <span className="board-act">
        {own && <button type="button" className="btn btn-secondary board-act-btn" disabled={offline} onClick={own.run}>{own.label}</button>}
        {act && (
          <button type="button" className={`btn ${act.kind === "gate" ? "btn-secondary" : "btn-primary"} board-act-btn`} disabled={offline} onClick={() => onAction(item, act)}>
            {act.label}
          </button>
        )}
      </span>
      {error && <p className="board-row-error" role="alert">{error}</p>}
    </div>
  );
}
