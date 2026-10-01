import type { ReactNode } from "react";
import { elapsedBetween, shortId } from "../../../format";
import type { KraftEvent, Policy } from "../../../types";
import type { ChainNode as GraphNode } from "../../graph/layout";
import type { Sel } from "../../graph/usePaneSelection";
import type { TaskKind } from "../../icons";
import { act } from "../actions";
import { footerState, isEscalation, stateWord } from "../nodeGraph";
import type { ItemDetail } from "../useItem";
import { ChainConfig, ChainOverview } from "./ChainPane";
import { NodeConfig, NodeOverview } from "./NodePane";
import { PathFooter } from "./PathFooter";

export type PaneArgs = {
  item: ItemDetail;
  events: KraftEvent[];
  now: number;
  policy: Policy | null;
  /** The chain canvas's nodes, for their drawn state. */
  graph: GraphNode[];
  sel: Sel;
  level: "chain" | "node";
  tab: string;
  reload: () => void;
  pick: (sel: Sel) => void;
  focus: (node: string) => void;
  editBudget: boolean;
  setEditBudget: (on: boolean) => void;
};
export type PaneContent = {
  crumbs: { label: string; onClick?: () => void }[];
  icon?: string;
  taskKind?: TaskKind;
  gate?: boolean;
  title: string;
  sub?: string;
  tabs?: { value: string; label: string }[];
  body: ReactNode;
  footer?: ReactNode;
};

const footerOf = (item: ItemDetail, path: string, what: "node" | "step" | "task", state: ReturnType<typeof footerState>, reload: () => void) =>
  state ? <PathFooter item={item} path={path} what={what} state={state} reload={reload} /> : undefined;

const OVERVIEW_CONFIG = [{ value: "overview", label: "Overview" }, { value: "config", label: "Config" }];

/** What the side pane shows for the selection (Decisions §5): the chain, a node, a step or a task. */
export function paneContent(a: PaneArgs): PaneContent {
  const { item, sel } = a;
  const toChain = { label: item.chain_template, onClick: () => a.pick({ kind: "chain" }) };
  if (sel.kind === "chain")
    return {
      crumbs: [{ label: item.bead_id || shortId(item.id) }],
      icon: "workflow",
      title: item.chain_template,
      sub: `this item's chain · ${item.chain_definition.nodes.length} nodes · frozen at intake`,
      tabs: OVERVIEW_CONFIG,
      body: a.tab === "config"
        ? <ChainConfig item={item} policy={a.policy} reload={a.reload} editBudget={a.editBudget} onEditBudget={a.setEditBudget} />
        : <ChainOverview item={item} events={a.events} now={a.now} onSelect={(node) => a.pick({ kind: "node", node })} />,
    };
  const node = item.chain_definition.nodes.find((n) => n.id === sel.node)!;
  const drawn = a.graph.find((g) => g.id === sel.node);
  const sessions = item.worker_sessions.filter((s) => s.node_id === node.id && !isEscalation(s));
  const started = sessions.map((s) => s.started_at).filter(Boolean).sort().at(-1);
  const live = drawn?.state === "current" && drawn.running;
  return {
    crumbs: [toChain],
    icon: (node.steps?.length ?? 0) > 1 ? "layers" : undefined,
    gate: node.kind === "gate",
    title: node.id,
    // A node the run stands on but that isn't running says why ("needs you", "paused").
    sub: `${node.kind === "gate" ? "gate" : "exec"} node · ${drawn?.state === "current" && !live && drawn.sub ? drawn.sub : stateWord(drawn?.state)}${live && started ? ` ${elapsedBetween(started, null, a.now)}` : ""}`,
    tabs: OVERVIEW_CONFIG,
    body: a.tab === "config"
      ? <NodeConfig item={item} node={node} onReset={async () => { const r = await act.patch(item.id, { node_overrides: { [node.id]: {} } }); if (r.ok) a.reload(); }} />
      : <NodeOverview item={item} node={node} onStep={(step) => a.pick({ kind: "step", node: node.id, step })} onNode={(n) => a.pick({ kind: "node", node: n })} />,
    footer: footerOf(item, node.id, "node", footerState(item, sessions), a.reload),
  };
}
