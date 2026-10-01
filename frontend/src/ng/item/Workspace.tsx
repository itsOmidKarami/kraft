import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { create } from "zustand";
import * as api from "../../api";
import { elapsed, shortId } from "../../format";
import type { Policy } from "../../types";
import { openingView } from "../graph/camera";
import { Inspector } from "../graph/Inspector";
import { StageGraph } from "../graph/StageGraph";
import { paneReducer, type PaneAction, type PaneState } from "../graph/usePaneSelection";
import { useResizable, useWidth } from "../graph/useResizable";
import { chainGraph } from "./graph";
import { ChainConfig, ChainOverview } from "./panes/ChainPane";
import { pushes, placeUrl, readPlace, type Place } from "./url";
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
  const { pane, setPane } = usePaneMemory();
  const [frame, canvasW] = useWidth();
  const size = useResizable(PAGE, canvasW);
  const events = useEvents(item.id, item.updated_at);
  const [policy, setPolicy] = useState<Policy | null>(null);
  useEffect(() => void api.getPolicy().then(setPolicy, () => setPolicy(null)), []);
  const [editBudget, setEditBudget] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  const state: PaneState = { level: place.node ? "node" : "chain", node: place.node, sel: place.sel, open: pane.open, userCollapsed: pane.userCollapsed };
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
    if (tabParam && tabParam !== lastTab.current && !pane.open) setPane({ open: true, userCollapsed: false });
    lastTab.current = tabParam;
  }, [tabParam, pane.open]);

  const graph = useMemo(() => chainGraph(item, events, now), [item, events, now]);
  const selectedNode = place.sel.kind === "chain" ? undefined : place.sel.node;
  const reserve = size.overlay ? 0 : pane.open ? size.width : 40;
  const cover = size.overlay && pane.open ? size.width : 0;
  const hasCurrent = graph.nodes.some((n) => n.state === "current" || n.state === "failed" || n.state === "amber");

  const sel = place.sel;
  const chainCrumb = item.bead_id || shortId(item.id);
  let body: JSX.Element;
  let head: { crumbs: { label: string; onClick?: () => void }[]; icon?: string; gate?: boolean; title: string; sub?: string; tabs?: { value: string; label: string }[] };
  const tab = place.tab ?? "overview";
  if (sel.kind === "chain") {
    head = { crumbs: [{ label: chainCrumb }], icon: "workflow", title: item.chain_template, sub: `this item's chain · ${nodes.length} nodes · frozen at intake`, tabs: [{ value: "overview", label: "Overview" }, { value: "config", label: "Config" }] };
    body = tab === "config"
      ? <ChainConfig item={item} policy={policy} reload={reload} editBudget={editBudget} onEditBudget={setEditBudget} />
      : <ChainOverview item={item} events={events} now={now} onSelect={(node) => dispatch({ type: "pick", sel: { kind: "node", node } })} />;
  } else {
    const n = nodes.find((x) => x.id === sel.node);
    const g = graph.nodes.find((x) => x.id === sel.node);
    const by = item.usage?.by_node.find((r) => r.node === sel.node);
    const word = ({ done: "done", current: "running", todo: "not started", failed: "failed", amber: "waiting for you", plain: "stopped" } as Record<string, string>)[g?.state ?? "plain"] ?? "";
    head = { crumbs: [{ label: item.chain_template, onClick: () => dispatch({ type: "pick", sel: { kind: "chain" } }) }], gate: n?.kind === "gate", title: sel.node, sub: `${n?.kind === "gate" ? "gate" : "exec"} node · ${word}`, tabs: [{ value: "overview", label: "Overview" }] };
    body = (
      <dl className="item-facts ip-facts">
        <div><dt>status</dt><dd>{[word, g?.meta, g?.sub].filter(Boolean).join(" · ")}</dd></div>
        {(n?.steps?.length ?? 0) > 0 && <div><dt>steps</dt><dd>{n!.steps!.length} · {n!.tasks.length} tasks</dd></div>}
        {g?.attempt && g.attempt > 1 && <div><dt>attempts</dt><dd>{g.attempt}</dd></div>}
        {by && <div><dt>ran</dt><dd>{by.sessions} sessions · {elapsed(by.wall_ms)}</dd></div>}
      </dl>
    );
  }

  return (
    <div className="item-canvas" ref={frame}>
      <StageGraph
        name={item.chain_template}
        nodes={graph.nodes}
        arcs={graph.arcs(selectedNode)}
        selected={selectedNode}
        opening={openingView(item.display_status ?? "", hasCurrent)}
        reserve={reserve}
        cover={cover}
        onSelect={(node) => dispatch({ type: "pick", sel: { kind: "node", node } })}
        onOpen={(node) => dispatch({ type: "expand", sel: { kind: "node", node } })}
        onEscape={() => dispatch({ type: "escape" })}
        onBackground={() => dispatch({ type: "background" })}
      />
      <Inspector
        id="item-pane"
        open={pane.open}
        size={size}
        crumbs={head.crumbs}
        icon={head.icon}
        gate={head.gate}
        title={head.title}
        sub={head.sub}
        tabs={head.tabs}
        tab={tab}
        onTab={(t) => go({ ...place, tab: t === "overview" ? undefined : t })}
        onCollapse={() => dispatch({ type: "collapse" })}
        onExpand={() => dispatch({ type: "expand" })}
      >
        {body}
      </Inspector>
    </div>
  );
}
