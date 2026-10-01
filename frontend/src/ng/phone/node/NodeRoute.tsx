import { useCallback, useEffect, useState } from "react";
import { Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useDocuments } from "../../item/useDocuments";
import { useEvents } from "../../item/useEvents";
import { useItem } from "../../item/useItem";
import { placeUrl, readPlace, type Place } from "../../item/url";
import { ScreenHeader } from "../nav/ScreenHeader";
import { NodeScreen } from "./NodeScreen";
import { TaskScreen } from "./TaskScreen";

/** `/work-items/:id/nodes/:node`: the node screen, or the task screen when `sel` names a task. The URL carries node, selection, tab and attempt (spec §6.2). */
export function NodeRoute() {
  const { id = "", node: nodeParam } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const loaded = useItem(id);
  const version = loaded.state === "ready" ? loaded.item.updated_at : "";
  const events = useEvents(id, version);
  const docs = useDocuments(id, version);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);
  const nodes = loaded.state === "ready" ? loaded.item.chain_definition.nodes : [];
  const place = readPlace(nodeParam, params, nodes);
  // A node change inside the screen is not history; entering the screen was.
  const setPlace = useCallback((patch: Partial<Place>) => navigate(placeUrl(id, { ...place, ...patch }), { replace: true }), [navigate, id, place]);
  if (loaded.state === "loading")
    return (
      <>
        <ScreenHeader />
        <div className="ph-content ph-skeleton" aria-busy="true"><span /><span /></div>
      </>
    );
  if (loaded.state === "missing")
    return (
      <>
        <ScreenHeader />
        <div className="ph-content"><h1 className="ph-title">Not found</h1><p className="ph-empty">This work item does not exist.</p></div>
      </>
    );
  if (!place.node) return <Navigate to={`/work-items/${encodeURIComponent(id)}`} replace />;
  const props = { item: loaded.item, events, docs, place, node: place.node, now, reload: loaded.reload, setPlace };
  if (place.sel.kind === "task") return <TaskScreen {...props} place={place as Place & { sel: { kind: "task"; node: string; step: string; task: string } }} />;
  return <NodeScreen {...props} />;
}
