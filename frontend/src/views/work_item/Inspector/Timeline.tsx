import { useEffect, useRef } from "react";
import { Row, RowText } from "../../../components/ui";
import { clock } from "../../../format";
import type { KraftEvent, WorkerSession } from "../../../types";
import { detailOf, groupByNode, titleOf } from "../timelineHelpers";
import { ScopeChips, type Scope } from "./Tasks";

/**
 * Inspector · Timeline (UI v2 · 05, 15; W11 · F). Under "this node" -- the
 * default, one scope with Tasks -- the selected node's events flat, newest
 * first, and one folded row for every other node; under "all", one row per
 * node that has run. `RightPane/Events.tsx` shows the selected node's events.
 * A selection is `node:seq` for one event, a bare `node` for a group.
 */
export function Timeline({
  events,
  sessions = [],
  nodeId,
  scope,
  onScope,
  selected,
  onSelect,
}: {
  events: KraftEvent[];
  sessions?: WorkerSession[];
  nodeId: string | null;
  scope: Scope;
  onScope: (s: Scope) => void;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const groups = groupByNode(events);
  const hooks = new Map(sessions.map((s) => [s.id, s.hook_point]));
  const own = groups.find((g) => g.node === nodeId);
  const ownAt = own ? groups.indexOf(own) : -1;
  const others = groups.filter((g) => g !== own);
  const selectedNode = selected?.split(":")[0] ?? null;
  const pickedEvent = selected?.includes(":") ?? false;

  // "this node" opens the right pane on the node's newest event (F.2). A link
  // to another node's group (a card's "see Timeline") shows that group under
  // "all" instead -- unless the person just chose "this node" themselves.
  const lastScope = useRef(scope);
  useEffect(() => {
    const scopeChanged = lastScope.current !== scope;
    lastScope.current = scope;
    if (scope !== "node") return;
    if (!scopeChanged && selected && !pickedEvent && selectedNode !== nodeId) {
      onScope("all");
      return;
    }
    if (pickedEvent && selectedNode === nodeId) return;
    const first = own?.events[0];
    if (first) onSelect(`${own.node}:${first.seq}`);
  });

  const showAll = () => {
    onScope("all");
    if (nodeId) onSelect(nodeId);
  };

  const count = scope === "node" ? (own?.events.length ?? 0) : events.length;
  const otherEvents = others.flatMap((g) => g.events);
  const times = otherEvents.map((e) => e.created_at).sort();
  const span = times.length ? `${clock(times[0])} – ${clock(times[times.length - 1])}` : "";
  // Groups run newest first: every other node sits after this one when this
  // is the newest node the item has run.
  const earlier = others.every((g) => groups.indexOf(g) > ownAt) ? "earlier" : "other";

  return (
    <div className="inspector-list" data-testid="inspector-timeline">
      <p className="section-label">
        EVENTS · {count}
        <ScopeChips scope={scope} onScope={onScope} nodeId={nodeId} />
      </p>
      {scope === "node" ? (
        <>
          {!own && <p className="empty">no events on this node yet</p>}
          {own?.events.map((e) => (
            <Row
              key={e.seq}
              data-testid={`timeline-event-${e.seq}`}
              data-selected={selected === `${own.node}:${e.seq}`}
              onClick={() => onSelect(`${own.node}:${e.seq}`)}
              columns="minmax(0, 1fr) auto"
            >
              <RowText title={titleOf(e, hooks) ?? e.type} sub={detailOf(e) ?? undefined} />
              <span className="row-sub">{clock(e.created_at)}</span>
            </Row>
          ))}
          {others.length > 0 && (
            <Row className="timeline-fold" data-testid="timeline-fold" onClick={showAll} columns="minmax(0, 1fr) auto">
              <RowText
                title={`▸ ${others.length} ${earlier} node${others.length === 1 ? "" : "s"} · ${otherEvents.length} events${span ? ` · ${span}` : ""}`}
              />
              <button
                type="button"
                className="btn btn-quiet"
                onClick={(e) => {
                  e.stopPropagation();
                  showAll();
                }}
              >
                show all
              </button>
            </Row>
          )}
        </>
      ) : groups.length === 0 ? (
        <p className="empty">no events yet</p>
      ) : (
        <>
          {groups.map((g) => (
            <Row
              key={g.node}
              data-selected={g.node === (selectedNode ?? nodeId)}
              onClick={() => onSelect(g.node)}
              columns="1fr auto"
            >
              <RowText title={g.node} sub={g.span} />
              <span className="row-sub">{g.events.length} events</span>
            </Row>
          ))}
          <p className="inspector-foot">{events.length} events total</p>
        </>
      )}
    </div>
  );
}
