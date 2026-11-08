import { ago, repoName, shortId } from "../../../format";
import type { WorkItem } from "../../../types";
import { chainLabel, chainOf, groupOf } from "../../board/model";
import { CUE_ICON } from "../../board/cue";
import { nodeWords, reasonTail, slicesLine, stopCueOf, ticksOf } from "../../board/rowText";
import { IN_YOUR_CHECKOUT } from "../../board/sharedWords";
import { NodeGlyph } from "../../graph/NodeGlyph";
import { Button } from "../../ui/Button";
import { breakPaths } from "../../ui/breakable";
import { cardButtons, type CardButton } from "./actions";
import { stateGlyph } from "./glyph";

export interface CardProps {
  item: WorkItem;
  now: number;
  offline: boolean;
  busy: boolean;
  error?: string | null;
  onOpen: () => void;
  /** A slice of a parent opens as the item it is. */
  onOpenChild: (id: string) => void;
  onButton: (b: CardButton) => void;
}

/** One board card (the prototype's Board rows): glyph, title and meta, the tick
 *  strip, the reason tail, and the pair of inline actions. */
export function Card({ item, now, offline, busy, error, onOpen, onOpenChild, onButton }: CardProps) {
  const g = stateGlyph(item);
  const hot = groupOf(item) === "needs";
  const cue = hot ? stopCueOf(item) : null;
  const Cue = cue ? CUE_ICON[cue] : null;
  const buttons = cardButtons(item);
  return (
    <article className="ph-card" data-row={item.id}>
      <button type="button" className="ph-card-main" onClick={onOpen}>
        <span className="ph-card-head">
          <NodeGlyph kind={g.kind} size="sm" state={g.state} icon={g.icon} running={g.running} />
          <span className="ph-card-text">
            <span className="ph-card-title">{item.title}</span>
            <span className="ph-card-meta">{repoName(item.repo)}{item.in_place && <> · <span className="ph-card-inplace">{IN_YOUR_CHECKOUT}</span></>} · {item.bead_id || shortId(item.id)} · {chainLabel(chainOf(item))} · {ago(item.updated_at, now)}</span>
          </span>
        </span>
        <span className="ph-ticks" aria-hidden="true">
          {ticksOf(item).map((t, k) => <span key={k} className={`ph-tick ph-tick-${t.state}${t.gate ? " ph-tick-gate" : ""}`} />)}
        </span>
        <span className={`ph-card-tail${hot && cue !== "paused" ? " ph-is-hot" : ""}${cue ? ` ph-is-${cue}` : ""}${g.running ? " ph-is-running" : ""}`}>
          {Cue && <Cue size={12} aria-hidden className="ph-card-cue" />}
          {breakPaths(reasonTail(item, now))}
        </span>
        {slicesLine(item) && <span className="ph-card-slices">{slicesLine(item)}</span>}
      </button>
      {buttons.length > 0 && (
        <div className="ph-card-actions">
          {buttons.map((b) => (
            <Button key={b.kind} className={`ph-btn ph-card-btn${b.variant === "primary" ? " ph-btn-primary" : ""}`} variant={b.variant} disabled={offline || busy} onClick={() => onButton(b)}>
              {b.label}
            </Button>
          ))}
        </div>
      )}
      {item.children && item.children.length > 0 && (
        <ul className="ph-card-children" aria-label={`Slices of ${item.title}`}>
          {item.children.map((c) => (
            <li key={c.id}>
              <button type="button" className="ph-card-child" onClick={() => onOpenChild(c.id)}>
                <span className="ph-card-child-title">{c.slice_key}. {c.title}</span>
                <span className="ph-card-child-status">{c.display_status.replace(/_/g, " ")}{c.current_node_id ? ` · ${nodeWords(c.current_node_id)}` : ""}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="ph-card-error" role="alert">{error}</p>}
    </article>
  );
}
