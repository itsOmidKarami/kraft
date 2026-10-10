import { useCallback, useEffect, useState } from "react";
import { Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useDocuments } from "../../item/useDocuments";
import { useEvents } from "../../item/useEvents";
import { runVersion, useItem } from "../../item/useItem";
import { loopStepPaths, materialized } from "../../item/chainValues";
import { asOfPass, FIX_LOOP } from "../../item/nodeGraph";
import { isScopeTask } from "../../item/scopeView";
import { placeUrl, readPlace, selPath, type Place } from "../../item/url";
import { Doc } from "../doc/Doc";
import { ScreenHeader } from "../nav/ScreenHeader";
import { NodeScreen } from "./NodeScreen";
import { ScopeScreen } from "./ScopeScreen";
import { TaskScreen } from "./TaskScreen";

/** `/work-items/:id/nodes/:node`: the node screen, or the task screen when `sel` names a task. The URL carries node, selection, tab and attempt (spec §6.2). */
export function NodeRoute() {
  const { id = "", node: nodeParam } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const loaded = useItem(id);
  const version = loaded.state === "ready" ? loaded.version : "";
  const events = useEvents(id, version);
  const docs = useDocuments(id, loaded.state === "ready" ? runVersion(loaded.item) : "");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);
  const nodes = loaded.state === "ready" ? loaded.item.chain_definition.nodes : [];
  const place = readPlace(nodeParam, params, nodes);
  // A node change inside the screen is not history; entering the screen was.
  // The pass and the fix-loop round are one node's: any way to another node leaves them behind.
  const setPlace = useCallback((patch: Partial<Place>) => navigate(placeUrl(id, { ...place, ...(patch.node && patch.node !== place.node ? { round: undefined, pass: undefined } : {}), ...patch }), { replace: true }), [navigate, id, place]);
  if (params.get("doc")) return <Doc id={params.get("doc")!} />;
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
  // A fix loop's own step is the desktop canvas's frame of its tasks: the phone has no screen for it, so its address opens the node.
  if (place.sel.kind === "task" && place.sel.step === FIX_LOOP && loopStepPaths(materialized(loaded.item), place.node, place.sel.task).length)
    return <Navigate to={placeUrl(id, { node: place.node, sel: { kind: "node", node: place.node } })} replace />;
  // An earlier pass of the node is read off the item as that pass left it. A task and a scope are all of one pass;
  // the node screen keeps the item as it stands for its strip, its state and its buttons.
  const seen = asOfPass(loaded.item, place.node, place.pass);
  const props = { item: seen, version, events, docs, place, node: place.node, now, reload: loaded.reload, setPlace };
  type OfTask = Place & { sel: { kind: "task"; node: string; step: string; task: string } };
  // A scope of a changed-test-scope task has its own screen; on any other task `scope` names nothing.
  if (place.sel.kind === "task" && place.scope && isScopeTask(loaded.item, selPath(place.sel)!)) return <ScopeScreen {...props} place={place as OfTask & { scope: string }} />;
  // On any other task a `scope` would only make Back name a screen that is not there: drop it.
  if (place.sel.kind === "task" && place.scope) return <Navigate to={placeUrl(id, { ...place, scope: undefined })} replace />;
  if (place.sel.kind === "task") return <TaskScreen {...props} place={place as OfTask} />;
  return <NodeScreen {...props} item={loaded.item} seen={seen} />;
}
