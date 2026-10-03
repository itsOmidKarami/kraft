import { ago, repoName, shortId } from "../../../format";
import type { WorkItem } from "../../../types";
import { chainOf, groupOf } from "../../board/model";
import { reasonTail, ticksOf } from "../../board/rowText";
import { NodeGlyph } from "../../graph/NodeGlyph";
import { Button } from "../../ui/Button";
import { cardButtons, type CardButton } from "./actions";
import { stateGlyph } from "./glyph";

export interface CardProps {
  item: WorkItem;
  now: number;
  offline: boolean;
  busy: boolean;
  error?: string | null;
  onOpen: () => void;
  onButton: (b: CardButton) => void;
}

/** One board card (the prototype's Board rows): glyph, title and meta, the tick
 *  strip, the reason tail, and the pair of inline actions. */
export function Card({ item, now, offline, busy, error, onOpen, onButton }: CardProps) {
  const g = stateGlyph(item);
  const hot = groupOf(item) === "needs";
  const buttons = cardButtons(item);
  return (
    <article className="ph-card" data-row={item.id}>
      <button type="button" className="ph-card-main" onClick={onOpen}>
        <span className="ph-card-head">
          <NodeGlyph kind={g.kind} size="sm" state={g.state} icon={g.icon} />
          <span className="ph-card-text">
            <span className="ph-card-title">{item.title}</span>
            <span className="ph-card-meta">{repoName(item.repo)} · {item.bead_id || shortId(item.id)} · {chainOf(item)} · {ago(item.updated_at, now)}</span>
          </span>
        </span>
        <span className="ph-ticks" aria-hidden="true">
          {ticksOf(item).map((t, k) => <span key={k} className={`ph-tick ph-tick-${t.state}${t.gate ? " ph-tick-gate" : ""}`} />)}
        </span>
        <span className={`ph-card-tail${hot ? " ph-is-hot" : ""}`}>{reasonTail(item, now)}</span>
      </button>
      {buttons.length > 0 && (
        <div className="ph-card-actions">
          {buttons.map((b) => (
            <Button key={b.kind} className={`ph-btn ph-card-btn${"primary" in b ? " ph-btn-primary" : ""}`} variant={"primary" in b ? "primary" : "secondary"} disabled={offline || busy} onClick={() => onButton(b)}>
              {b.label}
            </Button>
          ))}
        </div>
      )}
      {error && <p className="ph-card-error" role="alert">{error}</p>}
    </article>
  );
}
