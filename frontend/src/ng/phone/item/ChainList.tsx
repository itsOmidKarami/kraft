import { ChevronDown, ChevronRight, ChevronUp } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { KraftEvent } from "../../../types";
import { NodeGlyph } from "../../graph/NodeGlyph";
import { chainGraph } from "../../item/graph";
import { placeUrl } from "../../item/url";
import type { ItemDetail } from "../../item/useItem";
import { Block } from "../ui/Rows";
import { gateSkipped } from "../../item/events";
import { nodeSub } from "./model";

/** The vertical chain (W17 brief C.3): `N of M nodes`, a tick strip, the done
 *  nodes collapsed behind `N done`, then a 56px row per remaining node. */
export function ChainList({ item, events, now }: { item: ItemDetail; events: KraftEvent[]; now: number }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const { nodes } = chainGraph(item, events, now);
  const done = nodes.filter((n) => n.state === "done");
  const shown = open ? nodes : nodes.filter((n) => n.state !== "done");
  return (
    <Block title="Chain" aside={`${done.length} of ${nodes.length} nodes`}>
      <div className="ph-ticks ph-ticks-flush" aria-hidden="true">
        {nodes.map((n) => <span key={n.id} className={`ph-tick ph-tick-${n.state === "done" ? "done" : n.state === "failed" || n.capped ? "failed" : n.state === "current" ? (n.running ? "current" : "hot") : "todo"}${n.kind === "gate" ? " ph-tick-gate" : ""}`} />)}
      </div>
      <div className="ph-list">
        {done.length > 0 && (
          <button type="button" className="ph-row ph-done-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? <ChevronUp size={16} aria-hidden="true" /> : <ChevronDown size={16} aria-hidden="true" />}
            <span className="ph-row-label">{open ? "Hide " : ""}{done.length} done</span>
          </button>
        )}
        {shown.map((n) => {
          const sub = nodeSub(n, n.kind === "gate" && gateSkipped(events, n.id));
          return (
            <button key={n.id} type="button" className="ph-row ph-node-row" onClick={() => navigate(placeUrl(item.id, { node: n.id, sel: { kind: "node", node: n.id } }))}>
              <NodeGlyph kind={n.kind} size="sm" state={n.state} icon={n.icon} running={n.running} paused={n.paused} capped={n.capped} attempt={n.attempt} attemptStopped={n.attemptStopped} esc={n.esc} />
              <span className="ph-row-text">
                <span className="ph-row-label ph-mono">{n.id}</span>
                <span className={`ph-row-hint ph-tone-${sub.tone}`}>{sub.text}</span>
              </span>
              {n.state === "current" && n.sub === "needs you" && n.kind === "gate" && <span className="ph-pillbadge">you</span>}
              <ChevronRight size={16} className="ph-chev" aria-hidden="true" />
            </button>
          );
        })}
      </div>
    </Block>
  );
}
