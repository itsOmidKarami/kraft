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
import { AddNodeMenu } from "./draft/AddNodeMenu";
import { useDraft } from "./draft/context";
import { useApplied } from "./draft/useApplied";
import { markNodes, markSteps } from "./draft/draftGraph";
import { chainGraph } from "./graph";
import { DocViewer, docBy } from "./DocViewer";
import { gateView } from "./gateView";
import { nodeGraph } from "./nodeGraph";
import { taskName } from "./paths";
import { paneContent } from "./panes/paneContent";
import { pushes, placeUrl, readPlace, type Place } from "./url";
import { useDocuments } from "./useDocuments";
import { useEvents } from "./useEvents";
import type { ItemDetail } from "./useItem";
import { chainName } from "./chainName";

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
export function Workspace({ item: raw, reload }: { item: ItemDetail; reload: () => void }) {
  const draft = useDraft();
  const item = draft?.shown ?? raw;
  const navigate = useNavigate();
  const { node: nodeParam } = useParams();
  const [search] = useSearchParams();
  const nodes = item.chain_definition.nodes ?? [];
  const place = readPlace(nodeParam, search, nodes);
  const applied = useApplied(item.id, item.updated_at, place.tab === "config");
  const { pane: pane_, setPane } = usePaneMemory();
  const [frame, canvasW] = useWidth();
  const size = useResizable(PAGE, canvasW);
  const events = useEvents(item.id, item.updated_at);
  const docs = useDocuments(item.id, item.updated_at);
  const [artifact, setArtifact] = useState(false);
  const [policy, setPolicy] = useState<Policy | null>(null);
  useEffect(() => void api.getPolicy().then(setPolicy, () => setPolicy(null)), []);
  const [editBudget, setEditBudget] = useState(false);
  const [adding, setAdding] = useState<{ at: number; seam: HTMLElement } | null>(null);
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

  const marks = useMemo(() => ({ view: draft?.draft.view ?? null, pending: draft?.draft.pending ?? null, own: new Set(raw.chain_definition.nodes.map((n) => n.id)) }), [draft?.draft.view, draft?.draft.pending, raw]);
  const graph = useMemo(() => {
    const g = chainGraph(item, events, now);
    return draft ? { ...g, nodes: markNodes(g.nodes, marks) } : g;
  }, [item, events, now, draft, marks]);
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
    onDoc: (d: WorkItemDocument) => go({ ...place, doc: d.document_id }),
    onArtifact: () => setArtifact(true),
    canEdit: draft?.editable,
    applied,
  });
  const viewing = place.node ? nodes.find((n) => n.id === place.node) : undefined;
  const plain = viewing && nodeGraph(item, viewing, now);
  const inside = plain && draft ? { ...plain, steps: markSteps(plain.steps, viewing.id, marks) } : plain;
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
            name={chainName(item)}
            nodes={graph.nodes}
            arcs={graph.arcs(selectedNode)}
            seams={draft?.seams}
            onSeam={draft ? (at, seam) => setAdding({ at, seam }) : undefined}
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
          bar={pane.bar}
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
      {adding && draft && <AddNodeMenu at={adding.at} seam={adding.seam} onClose={() => setAdding(null)} />}
      {place.doc && <DocViewer source={{ kind: "document", id: place.doc, by: docBy(docs.find((d) => d.document_id === place.doc)) }} onClose={() => go({ ...place, doc: undefined })} />}
      {artifact && <DocViewer source={{ kind: "artifact", workItemId: item.id }} onClose={() => setArtifact(false)} />}
    </div>
  );
}
