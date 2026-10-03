import type { ReactNode } from "react";
import { elapsedBetween, shortId } from "../../../format";
import type { KraftEvent, Policy, WorkItemDocument } from "../../../types";
import type { ChainNode as GraphNode } from "../../graph/layout";
import type { Sel } from "../../graph/usePaneSelection";
import type { TaskKind } from "../../icons";
import { act } from "../actions";
import type { Applied } from "../draft/applied";
import { AppliedRows } from "../draft/AppliedRows";
import { DraftConfig } from "../draft/DraftConfig";
import { DraftNotes } from "../draft/DraftNotes";
import { ESCALATION, escalationsOf, footerState, isEscalation, lookWord, sessionLook, sessionsOf, stateWord } from "../nodeGraph";
import { stepsOf, taskName } from "../paths";
import type { ItemDetail } from "../useItem";
import { ChainConfig, ChainOverview } from "./ChainPane";
import { NodeConfig, NodeOverview } from "./NodePane";
import { GateBody, GateFooter } from "./GatePane";
import { Log } from "./Log";
import { PathFooter } from "./PathFooter";
import { AttemptSwitcher, TaskConfig, TaskInput, TaskOutput, TaskOverview } from "./TaskPane";
import { Thread } from "./Thread";
import { chainName } from "../chainName";
import { notStarted } from "../chainValues";
import { NodeOverrideRows } from "./ItemOverrides";

export type PaneArgs = {
  item: ItemDetail;
  /** Moves on every read of the item (useItem): what a pane reads beside the item is keyed on it. */
  version: string;
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
  attempt?: number;
  /** Pin the tabs to one attempt; undefined follows the newest. */
  setAttempt: (attempt: number | undefined) => void;
  docs: WorkItemDocument[];
  onDoc: (d: WorkItemDocument) => void;
  /** Open the pending gate's document (GET /artifact). */
  onArtifact: () => void;
  /** Whether the item draft may override this node (W11): true only after the node the run stands on. */
  canEdit?: (node: string) => boolean;
  /** What applied drafts set, by path (W11): shown in Config as "changed for this item". */
  applied?: Record<string, Applied>;
};
export type PaneContent = {
  crumbs: { label: string; onClick?: () => void }[];
  icon?: string;
  taskKind?: TaskKind;
  gate?: boolean;
  title: string;
  sub?: string;
  /** Above the tabs: the attempt every tab shows. */
  bar?: ReactNode;
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
  const toChain = { label: chainName(item), onClick: () => a.pick({ kind: "chain" }) };
  if (sel.kind === "chain")
    return {
      crumbs: [{ label: item.bead_id || shortId(item.id) }],
      icon: "workflow",
      title: chainName(item),
      sub: `this item's chain · ${item.chain_definition.nodes.length} nodes · frozen at intake`,
      tabs: OVERVIEW_CONFIG,
      body: (
        <>
          <DraftNotes />
          {a.tab === "config"
            ? <ChainConfig item={item} policy={a.policy} reload={a.reload} editBudget={a.editBudget} onEditBudget={a.setEditBudget} applied={a.applied} />
            : <ChainOverview item={item} events={a.events} now={a.now} onSelect={(node) => a.pick({ kind: "node", node })} docs={a.docs} onDoc={a.onDoc} />}
        </>
      ),
    };
  const node = item.chain_definition.nodes.find((n) => n.id === sel.node)!;
  if (sel.kind === "node" && node.kind === "gate") {
    const pending = item.pending_gate === node.id;
    return {
      crumbs: [toChain],
      gate: true,
      title: node.id,
      sub: `gate node · ${pending ? "waiting for you" : stateWord(a.graph.find((g) => g.id === node.id)?.state)}`,
      body: <GateBody item={item} version={a.version} gate={node} events={a.events} />,
      footer: pending ? <GateFooter item={item} gate={node} reload={a.reload} onRead={a.onArtifact} /> : undefined,
    };
  }
  const toNode = { label: node.id, onClick: () => a.pick({ kind: "node", node: node.id }) };
  if (sel.kind === "step") return stepPane(a, node, sel.step, [toChain, toNode]);
  if (sel.kind === "task") return taskPane(a, node, sel.step, sel.task, [toChain, toNode, { label: sel.step, onClick: sel.step === ESCALATION ? undefined : () => a.pick({ kind: "step", node: node.id, step: sel.step }) }]);
  const drawn = a.graph.find((g) => g.id === sel.node);
  const sessions = item.worker_sessions.filter((s) => s.node_id === node.id && !isEscalation(s));
  const started = sessions.map((s) => s.started_at).filter(Boolean).sort().at(-1);
  const live = drawn?.state === "current" && drawn.running;
  // Before the item starts, an exec node's own overrides are set in place.
  const fresh = notStarted(item) && node.kind !== "gate";
  return {
    crumbs: [toChain],
    icon: (node.steps?.length ?? 0) > 1 ? "layers" : undefined,
    gate: node.kind === "gate",
    title: node.id,
    // A node the run stands on but that isn't running says why ("needs you", "paused").
    sub: `${node.kind === "gate" ? "gate" : "exec"} node · ${drawn?.state === "current" && !live && drawn.sub ? drawn.sub : stateWord(drawn?.state)}${live && started ? ` ${elapsedBetween(started, null, a.now)}` : ""}`,
    tabs: OVERVIEW_CONFIG,
    body: (
      <>
        <DraftNotes node={node.id} />
        {a.tab === "config"
          ? <><NodeConfig item={item} node={node} controls={fresh} onReset={async () => { const r = await act.patch(item.id, { node_overrides: { [node.id]: {} } }); if (r.ok) a.reload(); }} />{fresh && <NodeOverrideRows item={item} node={node} policy={a.policy} reload={a.reload} />}<AppliedRows applied={a.applied} path={node.id} /><DraftConfig path={node.id} /></>
          : <NodeOverview item={item} node={node} onStep={(step) => a.pick({ kind: "step", node: node.id, step })} onNode={(n) => a.pick({ kind: "node", node: n })} />}
      </>
    ),
    footer: footerOf(item, node.id, "node", footerState(item, sessions), a.reload),
  };
}

type Crumbs = PaneContent["crumbs"];

/** A step's pane (Decisions §5): its tasks, which run in parallel, and its footer. */
function stepPane(a: PaneArgs, node: import("../../../types").ChainNode, stepId: string, crumbs: Crumbs): PaneContent {
  const { item } = a;
  const { steps, legacy } = stepsOf(node);
  const k = steps.findIndex((st) => st.id === stepId);
  const step = steps[k];
  if (!step) return { crumbs, title: stepId, body: <p className="item-muted">This step is not in the item's chain.</p> };
  const latest = step.tasks.map((p) => sessionsOf(item, p).at(-1));
  const status = latest.every((x) => x?.status.startsWith("done")) ? "done" : latest.some((x) => x && ["running", "pending"].includes(x.status)) ? "running" : latest.some(Boolean) ? "stopped" : "not started";
  const sessions = step.tasks.flatMap((p) => sessionsOf(item, p));
  return {
    crumbs,
    icon: "layers",
    title: stepId,
    sub: `step ${k + 1} of ${steps.length}`,
    tabs: OVERVIEW_CONFIG,
    body: a.tab === "config" ? (
      <>
        <dl className="item-facts ip-facts">
          {step.tasks.map((p) => <div key={p}><dt>task</dt><dd className="is-mono">{p}</dd></div>)}
        </dl>
        <AppliedRows applied={a.applied} path={`${node.id}.${stepId}`} />
        <DraftConfig path={`${node.id}.${stepId}`} />
      </>
    ) : (
      <>
        <dl className="item-facts ip-facts">
          <div><dt>tasks</dt><dd>{step.tasks.length === 1 ? "1 task" : `${step.tasks.length}, dispatched together`}</dd></div>
          <div><dt>status</dt><dd>{status}</dd></div>
          {node.on_failure?.length ? <div><dt>on failure</dt><dd>uses the node's</dd></div> : null}
        </dl>
        <h3 className="ip-h">Tasks{step.tasks.length > 1 ? " · run in parallel" : ""}</h3>
        <ul className="ip-list">
          {step.tasks.map((p, i) => {
            const look = sessionLook(latest[i], a.now);
            return (
              <li key={p}>
                <button type="button" className="ip-row" onClick={() => a.pick({ kind: "task", node: node.id, step: stepId, task: taskName(p) })}>
                  <span className={`ip-mark${look.running ? " is-live" : ""}`} aria-hidden>{look.state === "done" ? "✓" : look.running ? "●" : "○"}</span>
                  <span className="is-mono">{taskName(p)}</span>
                  <span className="ip-row-meta">{latest[i]?.model ? "agent" : stateWord(look.state)}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </>
    ),
    // A legacy node's steps have no path of their own: retry and skip it at node level.
    footer: legacy ? undefined : footerOf(item, `${node.id}.${stepId}`, "step", footerState(item, sessions), a.reload),
  };
}

const TASK_TABS = [{ value: "overview", label: "Overview" }, { value: "input", label: "Input" }, { value: "output", label: "Output" }, { value: "log", label: "Log" }, { value: "config", label: "Config" }];

/** A task's pane (Decisions §5 Pane tabs): the attempt switcher above the tabs,
 *  so one attempt drives Overview, Input, Output, Log and Config, with Thread
 *  first on the escalation task. */
function taskPane(a: PaneArgs, node: import("../../../types").ChainNode, stepId: string, task: string, crumbs: Crumbs): PaneContent {
  const { item } = a;
  const esc = stepId === ESCALATION;
  const path = esc ? ESCALATION : `${node.id}.${stepId}.${task}`;
  const sessions = esc ? escalationsOf(item, node.id) : sessionsOf(item, path);
  const at = sessions.find((s) => s.attempt === a.attempt) ?? sessions.at(-1);
  const look = sessionLook(at, a.now);
  const kind = esc || at?.model ? "agent" : undefined;
  const tabs = esc ? [{ value: "thread", label: "Thread" }, ...TASK_TABS] : TASK_TABS;
  const tab = tabs.some((t) => t.value === a.tab) ? a.tab : tabs[0].value;
  const head = { crumbs, taskKind: kind as TaskKind | undefined, icon: esc ? "siren" : undefined, title: task, sub: `${esc ? "escalation · " : ""}${kind ? `${kind} ` : ""}task · ${look.running ? (look.meta ?? "running") : lookWord(look)}` };
  if (!at) {
    // A task that has not run has only its Config, and only while the draft may override it (W11).
    const edit = !esc && !!a.canEdit?.(node.id);
    return {
      ...head,
      tabs: edit ? OVERVIEW_CONFIG : undefined,
      body: edit && a.tab === "config" ? <DraftConfig path={path} /> : <p className="item-muted">Not started. Its tabs fill in once it runs.</p>,
      footer: undefined,
    };
  }
  const current = item.current_node_id === node.id;
  // Stepping onto the newest attempt drops the pin, so the pane follows the next one that starts;
  // an older attempt stays put while newer ones arrive, and the switcher's count shows them.
  const switcher = <AttemptSwitcher sessions={sessions} at={at} onAt={(n) => a.setAttempt(n === sessions.at(-1)!.attempt ? undefined : n)} now={a.now} turns={esc} />;
  const bodies: Record<string, ReactNode> = {
    thread: <Thread item={item} version={a.version} node={node.id} upTo={at === sessions.at(-1) ? undefined : at} reload={a.reload} onNode={(n) => a.pick({ kind: "node", node: n })} />,
    overview: <TaskOverview path={path} s={at} docs={a.docs} onDoc={a.onDoc} />,
    input: <TaskInput item={item} s={at} current={current} />,
    output: <TaskOutput item={item} s={at} docs={a.docs} onDoc={a.onDoc} />,
    log: <Log key={at.id} sessionId={at.id} running={look.running === true} title={task} />,
    config: <><TaskConfig path={path} s={at} /><AppliedRows applied={a.applied} path={path} /></>,
  };
  const live = sessions.some((s) => ["running", "pending"].includes(s.status));
  return {
    ...head,
    // One attempt has nothing to switch between: the subtitle already says how it went.
    bar: sessions.length > 1 ? switcher : undefined,
    tabs,
    body: bodies[tab],
    footer: esc
      // The escalation's footer: stop it while it runs (GAP §2 #13); retry the node with a steer once it answered (#12).
      ? <PathFooter item={item} path={node.id} what="node" state={live ? null : footerState(item, item.worker_sessions.filter((s) => s.node_id === node.id && !isEscalation(s)))} reload={a.reload}
          extra={live ? <button type="button" className="btn btn-secondary" onClick={async () => { const r = await act.stopEscalation(item.id); if (r.ok) a.reload(); }}>Stop escalation</button> : undefined} />
      : footerOf(item, path, "task", footerState(item, sessions), a.reload),
  };
}
