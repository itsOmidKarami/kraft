import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { create } from "zustand";
import * as api from "../../api";
import type { Policy, WorkItemDocument } from "../../types";
import { openingView } from "../graph/camera";
import { Inspector } from "../graph/Inspector";
import { StageGraph } from "../graph/StageGraph";
import { ChainStrip } from "../graph/ChainStrip";
import { GateView } from "../graph/GateView";
import { NodeGraph, type NodeSel } from "../graph/NodeGraph";
import { paneReducer, type PaneAction, type PaneState, type Sel } from "../graph/usePaneSelection";
import { useResizable, useWidth } from "../graph/useResizable";
import { chainGraph } from "./graph";
import { DocViewer } from "./DocViewer";
import { gateView } from "./gateView";
import { nodeGraph } from "./nodeGraph";
import { taskName } from "./paths";
import { paneContent } from "./panes/paneContent";
import { pushes, placeUrl, readPlace, type Place } from "./url";
import { useDocuments } from "./useDocuments";
import { useEvents } from "./useEvents";
import type { ItemDetail } from "./useItem";

const PAGE = "item";

/** Whether the pane is open, kept across items for the session (spec §6.2:
 *  a collapse survives moving between items); the page remounts per item. */
export const usePaneMemory = create<{ pane: { open: boolean; userCollapsed: boolean }; setPane: (p: { open: boolean; userCollapsed: boolean }) => void }>((set) => ({
  pane: { open: true, userCollapsed: false },
  setPane: (pane) => set({ pane }),
}));

/** The canvas and its side pane. The URL holds where the person is (node
 *  view, selection, tab, attempt: spec §6.2); this keeps only whether the pane
 *  is open, which survives moving between items within a session. */
export function Workspace({ item, reload }: { item: ItemDetail; reload: () => void }) {
  const navigate = useNavigate();
  const { node: nodeParam } = useParams();
  const [search] = useSearchParams();
  const nodes = item.chain_definition.nodes ?? [];
  const place = readPlace(nodeParam, search, nodes);
  const { pane: pane_, setPane } = usePaneMemory();
  const [frame, canvasW] = useWidth();
  const size = useResizable(PAGE, canvasW);
  const events = useEvents(item.id, item.updated_at);
  const docs = useDocuments(item.id, item.updated_at);
  const [doc, setDoc] = useState<WorkItemDocument | null>(null);
  const [artifact, setArtifact] = useState(false);
  const [policy, setPolicy] = useState<Policy | null>(null);
  useEffect(() => void api.getPolicy().then(setPolicy, () => setPolicy(null)), []);
  const [editBudget, setEditBudget] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  const state: PaneState = { level: place.node ? "node" : "chain", node: place.node, sel: place.sel, open: pane_.open, userCollapsed: pane_.userCollapsed };
  const go = (to: Place) => {
    const url = placeUrl(item.id, to);
    if (url !== placeUrl(item.id, place)) navigate(url, { replace: !pushes(place, to) });
  };
  const dispatch = (a: PaneAction, extra: Partial<Place> = {}) => {
    const next = paneReducer(state, a);
    setPane({ open: next.open, userCollapsed: next.userCollapsed });
    const moved = JSON.stringify(next.sel) !== JSON.stringify(place.sel) || next.node !== place.node;
    go({ node: next.level === "node" ? next.node : undefined, sel: next.sel, tab: moved ? undefined : place.tab, attempt: moved ? undefined : place.attempt, ...extra });
  };
  // Item settings, Raise cap and the capped Resume land on the chain's Config with the pane open.
  const tabParam = search.get("tab");
  const lastTab = useRef(tabParam);
  useEffect(() => {
    if (tabParam && tabParam !== lastTab.current && !pane_.open) setPane({ open: true, userCollapsed: false });
    lastTab.current = tabParam;
  }, [tabParam, pane_.open]);

  // Entering or leaving a node view swaps the canvas under the focus: hand it to
  // the new canvas's Tab stop, so the keyboard path goes on (R6).
  const areaRef = useRef<HTMLDivElement>(null);
  const lastNode = useRef(place.node);
  useEffect(() => {
    if (lastNode.current === place.node) return;
    lastNode.current = place.node;
    const f = requestAnimationFrame(() => areaRef.current?.querySelector<HTMLElement>('[role="group"] [tabindex="0"]')?.focus());
    return () => cancelAnimationFrame(f);
  }, [place.node]);

  const graph = useMemo(() => chainGraph(item, events, now), [item, events, now]);
  const selectedNode = place.sel.kind === "chain" ? undefined : place.sel.node;
  const reserve = size.overlay ? 0 : pane_.open ? size.width : 40;
  const cover = size.overlay && pane_.open ? size.width : 0;
  const hasCurrent = graph.nodes.some((n) => n.state === "current" || n.state === "failed" || n.state === "amber");

  const sel = place.sel;
  const pick = (to: Sel) => dispatch({ type: "pick", sel: to });
  const tab = place.tab ?? "";
  const pane = paneContent({
    item, events, now, policy, graph: graph.nodes, sel, level: state.level, tab, reload, pick, editBudget, setEditBudget, docs,
    focus: (node) => dispatch({ type: "focus", node }),
    attempt: place.attempt,
    setAttempt: (attempt) => go({ ...place, attempt }),
    onDoc: setDoc,
    onArtifact: () => setArtifact(true),
  });
  const viewing = place.node ? nodes.find((n) => n.id === place.node) : undefined;
  const inside = viewing && nodeGraph(item, viewing, now);
  const nodeSel = (x: NodeSel): Sel => (x.task ? { kind: "task", node: viewing!.id, step: x.step, task: x.task } : { kind: "step", node: viewing!.id, step: x.step });

  return (
    <div className="item-canvas" ref={frame}>
      {viewing && <ChainStrip nodes={graph.nodes} viewing={viewing.id} onOpen={(node) => dispatch({ type: "focus", node })} onBack={() => dispatch({ type: "back" })} />}
      <div className="item-area" ref={areaRef}>
        {viewing?.kind === "gate" ? (
          <GateView {...gateView(item, viewing, events, now, sel, { doc: () => setArtifact(true), reject: (to) => dispatch({ type: "focus", node: to }) })} right={reserve}
            onGate={() => pick({ kind: "node", node: viewing.id })}
            onReviewer={() => { const t = viewing.tasks[0]; if (t) pick({ kind: "task", node: viewing.id, step: t.split(".")[1], task: taskName(t) }); }}
            onBackground={() => dispatch({ type: "background" })}
          />
        ) : viewing && inside ? (
          <NodeGraph
            name={viewing.id}
            steps={inside.steps}
            side={inside.side}
            loop={inside.loop}
            onFailure={inside.onFailure}
            reserve={reserve}
            selected={sel.kind === "task" || sel.kind === "step" ? { step: sel.step, task: sel.kind === "task" ? sel.task : undefined } : undefined}
            onSelect={(x) => pick(nodeSel(x))}
            onOpen={(x) => dispatch({ type: "expand", sel: nodeSel(x) })}
            onExpand={(x) => dispatch({ type: "expand", sel: nodeSel(x) })}
            onEscape={() => dispatch({ type: "escape" })}
            onBackground={() => dispatch({ type: "background" })}
          />
        ) : (
          <StageGraph
            name={item.chain_template}
            nodes={graph.nodes}
            arcs={graph.arcs(selectedNode)}
            selected={selectedNode}
            opening={openingView(item.display_status ?? "", hasCurrent)}
            reserve={reserve}
            cover={cover}
            onSelect={(node) => pick({ kind: "node", node })}
            onOpen={(node) => dispatch({ type: "expand", sel: { kind: "node", node } })}
            onFocusNode={(node) => dispatch({ type: "focus", node })}
            onEscape={() => dispatch({ type: "escape" })}
            onBackground={() => dispatch({ type: "background" })}
          />
        )}
        <Inspector
          id="item-pane"
          open={pane_.open}
          size={size}
          crumbs={pane.crumbs}
          icon={pane.icon}
          taskKind={pane.taskKind}
          gate={pane.gate}
          title={pane.title}
          sub={pane.sub}
          tabs={pane.tabs}
          // The pane's first tab when the URL names none (Thread, on an escalation).
          tab={pane.tabs?.some((t) => t.value === tab) ? tab : pane.tabs?.[0]?.value}
          onTab={(t) => go({ ...place, tab: t === pane.tabs?.[0]?.value ? undefined : t })}
          onCollapse={() => dispatch({ type: "collapse" })}
          onExpand={() => dispatch({ type: "expand" })}
          onFocus={state.level === "chain" && sel.kind === "node" ? () => dispatch({ type: "focus", node: sel.node }) : undefined}
          footer={pane.footer}
        >
          {pane.body}
        </Inspector>
      </div>
      {doc && <DocViewer source={{ kind: "document", id: doc.document_id, by: [doc.node_id, doc.hook_point?.split(".").at(-1), doc.attempt ? `attempt ${doc.attempt}` : ""].filter(Boolean).join(" › ") }} onClose={() => setDoc(null)} />}
      {artifact && <DocViewer source={{ kind: "artifact", workItemId: item.id }} onClose={() => setArtifact(false)} />}
    </div>
  );
}
