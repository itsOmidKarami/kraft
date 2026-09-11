import { Row, RowText } from "../../../components/ui";
import type { KraftEvent } from "../../../types";
import { groupByNode } from "../timelineHelpers";

/**
 * Inspector · Timeline (UI v2 · 05, 15): one row per node that has run,
 * newest first, with a duration and an event count. Selecting a row is what
 * `RightPane/Events.tsx` shows.
 */
export function Timeline({
  events,
  selected,
  onSelect,
}: {
  events: KraftEvent[];
  selected: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const groups = groupByNode(events);
  if (groups.length === 0) return <p className="empty">no events yet</p>;
  return (
    <div className="inspector-list" data-testid="inspector-timeline">
      {groups.map((g) => (
        <Row
          key={g.node}
          data-selected={g.node === selected}
          onClick={() => onSelect(g.node)}
          columns="1fr auto"
        >
          <RowText title={g.node} sub={g.span} />
          <span className="row-sub">{g.events.length} events</span>
        </Row>
      ))}
      <p className="inspector-foot">{events.length} events total</p>
    </div>
  );
}
