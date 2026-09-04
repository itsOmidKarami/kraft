import { useEffect } from "react";
import { Link, useParams } from "react-router-dom";
import { ChainStrip } from "../components/ChainStrip";
import { CurrentNodePanel } from "../components/CurrentNodePanel";
import { EventTimeline } from "../components/EventTimeline";
import { LinkedDocuments } from "../components/LinkedDocuments";
import { useStore } from "../store";

export function WorkItemDetail() {
  const { id = "" } = useParams();
  const item = useStore((s) => s.workItems[id]);
  const sessions = useStore((s) => s.sessionsByItem[id] ?? []);
  const events = useStore((s) => s.eventsByItem[id] ?? []);
  const hydrateItem = useStore((s) => s.hydrateItem);

  useEffect(() => {
    hydrateItem(id).catch(() => {});
    const t = setInterval(() => hydrateItem(id).catch(() => {}), 60_000);
    return () => clearInterval(t);
  }, [id, hydrateItem]);

  if (!item) return <p className="empty">unknown work item</p>;

  return (
    <div className="detail">
      <Link to="/">← board</Link>
      <h2>{item.title}</h2>
      <ChainStrip item={item} size="lg" />
      <CurrentNodePanel item={item} sessions={sessions} />
      <LinkedDocuments workItemId={id} eventCount={events.length} />
      <EventTimeline events={events} />
    </div>
  );
}
