import type { ReactNode } from "react";
import { elapsed, elapsedBetween, shortId } from "../../../format";
import type { KraftEvent, Policy, WorkItemDocument } from "../../../types";
import type { ChainNode as GraphNode } from "../../graph/layout";
import type { Sel } from "../../graph/usePaneSelection";
import type { TaskKind } from "../../icons";
import { act } from "../actions";
import type { Applied } from "../draft/applied";
import { AppliedRows } from "../draft/AppliedRows";
import { DraftConfig } from "../draft/DraftConfig";
import { DraftNotes } from "../draft/DraftNotes";
import { AUTO_REVIEW, ESCALATION, escalationsOf, FIX_LOOP, footerState, isEscalation, JUDGE, lookWord, loopRounds, sessionLook, sessionsOf, stateWord } from "../nodeGraph";
import { stepsOf, taskName } from "../paths";
import type { ItemDetail } from "../useItem";
import { ChainConfig, ChainOverview } from "./ChainPane";
import { NodeConfig, NodeOverview } from "./NodePane";
import { GateBody, GateFooter } from "./GatePane";
import { Log } from "./Log";
import { PathFooter } from "./PathFooter";
import { AttemptMenu, TaskConfig, TaskInput, TaskOutput, TaskOverview } from "./TaskPane";
import { Thread } from "./Thread";
import { chainName } from "../chainName";
import { loopStepPaths, materialized, notStarted, planTaskPath, taskAt, taskKindAt } from "../chainValues";
import { NodeOverrideRows } from "./ItemOverrides";
import { scopePane, ScopeOverview } from "./ScopePane";
import { isScopeTask, scopesView } from "../scopeView";

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
  /** The fix-loop round the canvas shows, 1-based, when the node has a loop that ran. */
  round?: number;
  /** The scope of an open changed-test-scope task that is picked: its chip's key. */
  scope?: string;
  attempt?: number;
  /** Pin the tabs to one attempt; undefined follows the newest. */
  setAttempt: (attempt: number | undefined) => void;
  docs: WorkItemDocument[];
  onDoc: (d: WorkItemDocument) => void;
  /** Open the pending gate's document (GET /artifact). */
  onArtifact: () => void;
  /** Open the document the task at `path` produced, by its kind (GET /artifacts/{kind}). */
  onProduced: (kind: string, path: string) => void;
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
  /** A string, but for a task with more than one attempt: then it holds the attempt menu. */
  sub?: ReactNode;
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
  // A fix loop's own step, selected as its id where a loop task is `<step>.<task>`: the canvas draws one of several tasks as one box.
  const loopStep = sel.kind === "task" && sel.step === FIX_LOOP ? loopStepPane(a, node, sel.task, [toChain, toNode, { label: FIX_LOOP }]) : null;
  if (loopStep) return loopStep;
  // A task of a loop step of several leads back to that step, which the canvas draws as its frame.
  const up = sel.kind === "task" && sel.step === FIX_LOOP ? sel.task.split(".")[0] : "";
  const toLoopStep = up && loopStepPaths(materialized(item), node.id, up).length > 1 ? [{ label: up, onClick: () => a.pick({ kind: "task", node: node.id, step: FIX_LOOP, task: up }) }] : [];
  if (sel.kind === "task") return taskPane(a, node, sel.step, sel.task, [toChain, toNode, { label: sel.step, onClick: sel.step === ESCALATION || sel.step === FIX_LOOP || (node.kind === "gate" && sel.step === AUTO_REVIEW) ? undefined : () => a.pick({ kind: "step", node: node.id, step: sel.step }) }, ...toLoopStep]);
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
    // A node the run stands on but that isn't running says why: "needs you", "paused", "stopped at the cap",
    // "waiting on CI" (R11b-04: those last two read "running").
    sub: `${node.kind === "gate" ? "gate" : "exec"} node · ${currentWords(drawn, live)}${live && started ? ` ${elapsedBetween(started, null, a.now)}` : ""}`,
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

/** A node's state in the pane's subtitle, the phone's words for the same node (`phone/item/model`'s `nodeSub`). */
function currentWords(drawn: GraphNode | undefined, live: boolean | undefined): string {
  if (drawn?.state !== "current" || live) return stateWord(drawn?.state);
  if (drawn.capped) return "stopped at the cap";
  return [drawn.wait, drawn.sub].filter(Boolean).join(" · ") || stateWord(drawn.state);
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
  const status = stepStatus(latest);
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
        <StepTasks paths={step.tasks} latest={latest} now={a.now} onTask={(p) => a.pick({ kind: "task", node: node.id, step: stepId, task: taskName(p) })} />
      </>
    ),
    // A legacy node's steps have no path of their own: retry and skip it at node level.
    footer: legacy ? undefined : footerOf(item, `${node.id}.${stepId}`, "step", footerState(item, sessions), a.reload),
  };
}

type Latest = ReturnType<typeof sessionsOf>[number] | undefined;
/** What a step's tasks came to, from the newest session of each. */
const stepStatus = (latest: Latest[]) => (latest.every((x) => x?.status.startsWith("done")) ? "done" : latest.some((x) => x && ["running", "pending"].includes(x.status)) ? "running" : latest.some(Boolean) ? "stopped" : "not started");

/** A step's tasks as rows, each opening its task. */
function StepTasks({ paths, latest, now, onTask }: { paths: string[]; latest: Latest[]; now: number; onTask: (path: string) => void }) {
  return (
    <>
      <h3 className="ip-h">Tasks{paths.length > 1 ? " · run in parallel" : ""}</h3>
      <ul className="ip-list">
        {paths.map((p, i) => {
          const look = sessionLook(latest[i], now);
          return (
            <li key={p}>
              <button type="button" className="ip-row" onClick={() => onTask(p)}>
                <span className={`ip-mark${look.running ? " is-live" : ""}`} aria-hidden>{look.state === "done" ? "✓" : look.running ? "●" : "○"}</span>
                <span className="is-mono">{taskName(p)}</span>
                <span className="ip-row-meta">{latest[i]?.model ? "agent" : stateWord(look.state)}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </>
  );
}

/** A fix loop's own step: its tasks, which run in parallel, as the round shown ran them (a repair goes between that
 *  round and the next). It has no Config and no footer: the server addresses nothing under a fix loop (`actionPath`).
 *  Null when the loop has no step of that id, so a loop task's own id falls through to its pane. */
function loopStepPane(a: PaneArgs, node: import("../../../types").ChainNode, stepId: string, crumbs: Crumbs): PaneContent | null {
  const { item } = a;
  const paths = loopStepPaths(materialized(item), node.id, stepId);
  if (!paths.length) return null;
  const r = loopRounds(item, node) && a.round;
  const latest = paths.map((p) => sessionsOf(item, p).filter((s) => !r || s.round === r).at(-1));
  return {
    crumbs,
    icon: "layers",
    title: stepId,
    sub: `fix-loop step${r ? ` · between rounds ${r} and ${r + 1}` : ""} · ${stepStatus(latest)}`,
    body: (
      <>
        <dl className="item-facts ip-facts">
          <div><dt>tasks</dt><dd>{paths.length === 1 ? "1 task" : `${paths.length}, dispatched together`}</dd></div>
        </dl>
        <StepTasks paths={paths} latest={latest} now={a.now} onTask={(p) => a.pick({ kind: "task", node: node.id, step: FIX_LOOP, task: p.split(".").slice(2).join(".") })} />
      </>
    ),
  };
}

const TASK_TABS = [{ value: "overview", label: "Overview" }, { value: "input", label: "Input" }, { value: "output", label: "Output" }, { value: "log", label: "Log" }, { value: "config", label: "Config" }];

/** A task's pane (Decisions §5 Pane tabs): the attempt menu in the subtitle,
 *  so one attempt drives Overview, Input, Output, Log and Config, with Thread
 *  first on the escalation task. */
function taskPane(a: PaneArgs, node: import("../../../types").ChainNode, stepId: string, task: string, crumbs: Crumbs): PaneContent {
  const { item } = a;
  const esc = stepId === ESCALATION;
  // A gate's reviewer is no step of the chain: its sessions run at `<gate>.auto_review`, and the server addresses nothing under it.
  const rev = node.kind === "gate" && stepId === AUTO_REVIEW;
  const path = esc ? ESCALATION : rev ? `${node.id}.${AUTO_REVIEW}` : `${node.id}.${stepId}.${task}`;
  // One of an open changed-test-scope task's scopes has its own pane.
  if (a.scope && !esc && !rev && stepId !== FIX_LOOP) {
    return scopePane({ item, node, step: stepId, task, scope: a.scope, round: a.round ?? 1, now: a.now, crumbs, tab: a.tab, toTask: () => a.pick({ kind: "task", node: node.id, step: stepId, task }) });
  }
  // In a fix-loop node a task's pane is one round's: the step tasks and the judge as the round measured
  // (the judge after it), the repair as it went on to the next.
  const loop = !esc && !rev && stepId === FIX_LOOP ? (task === JUDGE ? "judge" : "repair") : null;
  const rounds = !esc && !rev ? loopRounds(item, node) : undefined;
  const r = rounds && a.round;
  const all = esc ? escalationsOf(item, node.id) : sessionsOf(item, path);
  const sessions = r ? all.filter((s) => s.round === (loop === "repair" ? r : r - 1)) : all;
  const at = sessions.find((s) => s.attempt === a.attempt) ?? sessions.at(-1);
  const look = sessionLook(at, a.now);
  const frozen = materialized(item);
  const kind = esc ? "agent" : taskKindAt(frozen, path) ?? (at?.model ? "agent" : undefined);
  const tabs = esc ? [{ value: "thread", label: "Thread" }, ...TASK_TABS] : TASK_TABS;
  const tab = tabs.some((t) => t.value === a.tab) ? a.tab : tabs[0].value;
  const place = r ? (loop === "judge" ? `fix-loop judge · after round ${r}` : loop === "repair" ? `fix-loop repair · between rounds ${r} and ${r + 1}` : `${kind ? `${kind} ` : ""}task · round ${r}${rounds.total ? ` of ${rounds.total}` : ""}`) : null;
  const lead = place ?? `${esc ? "escalation · " : ""}${kind ? `${kind} ` : ""}task`;
  // A round's task says how long it took, as the words run on: "running 41s", "done 31s".
  const took = r && at ? (look.running ? look.meta?.replace(" · ", " ") : look.state === "done" && at.wall_ms != null ? `done ${elapsed(at.wall_ms)}` : undefined) : undefined;
  // A task the round did not run says so in so many words, as the prototype has it.
  const state = took ?? (look.running ? (look.meta ?? "running") : at || !r ? lookWord(look) : loop === "judge" && r === 1 ? "skipped · the first repair runs without the judge" : "not run in this round");
  const head = { crumbs, taskKind: kind as TaskKind | undefined, icon: esc ? "siren" : undefined, title: loop ? taskName(task) : task, sub: `${lead} · ${state}` };
  if (!at) {
    // A task that has not run keeps its tabs, each saying why it is empty (LV-5). Config is the
    // draft's while it may override the task (W11). On the node the run stands on it can be skipped
    // before it runs (`/skip` takes a task of the current node).
    const edit = !esc && !rev && !!a.canEdit?.(node.id);
    // The fix loop's repair and judge do not wait on a step before them: a round either ran them or did not.
    const empty = (text: string) => <p className="item-muted">{loop ? "Not run in this round." : text}</p>;
    const bodies: Record<string, ReactNode> = {
      thread: empty("No turns yet."),
      overview: empty("Not run yet. It starts when the step before this one finishes."),
      input: empty("No input yet. It is read when the task starts."),
      output: empty("No output yet. It is written when the task finishes."),
      log: empty("No log yet. It starts when the step before this one finishes."),
      config: edit ? <DraftConfig path={path} /> : <><dl className="item-facts ip-facts"><div><dt>path</dt><dd className="is-mono">{path}</dd></div></dl><AppliedRows applied={a.applied} path={path} /></>,
    };
    // `/skip` takes no path under a fix loop (422), so its tasks have no Skip.
    const here = !esc && !rev && !loop && item.current_node_id === node.id;
    return {
      ...head,
      tabs,
      body: bodies[tab],
      footer: here ? <PathFooter item={item} path={path} what="task" state={item.display_status === "paused" ? "paused" : "running"} reload={a.reload} only="skip" /> : undefined,
    };
  }
  const current = item.current_node_id === node.id;
  // A changed-test-scope task runs one session per scope: they are its scopes, not attempts at it.
  const scopes = !esc && !rev && !loop && isScopeTask(item, path);
  // Picking the newest attempt drops the pin, so the pane follows the next one that starts;
  // an older attempt stays put while newer ones arrive, and the menu's count shows them.
  const menu = <AttemptMenu sessions={sessions} at={at} onAt={(n) => a.setAttempt(n === sessions.at(-1)!.attempt ? undefined : n)} now={a.now} turns={esc} inRound={!!r} />;
  // The plan's sub-tasks, on the one task that works through it, whichever attempt is picked.
  const progress = planTaskPath(frozen) === path ? item.progress : null;
  // The document the task writes is its output: one file, rewritten by each attempt, so offered on the newest
  // only, once it is done. A node's own tasks only, the ones GET /artifacts/{kind} reads for (`ResolvedNode.produces`).
  const produces = (!esc && !rev && !loop && at === sessions.at(-1) && at.status === "done" && frozen && taskAt(frozen, path)?.produces) || undefined;
  const bodies: Record<string, ReactNode> = {
    thread: <Thread item={item} version={a.version} node={node.id} upTo={at === sessions.at(-1) ? undefined : at} reload={a.reload} onNode={(n) => a.pick({ kind: "node", node: n })} />,
    overview: (
      <>
        {/* The changed-test-scope task says how its round went, and what its canvas frame means, above the usual facts. */}
        {scopes && <ScopeOverview view={scopesView(item, path, a.round ?? 1, a.now)} />}
        <TaskOverview path={path} s={at} docs={a.docs} onDoc={a.onDoc} progress={progress} running={sessionLook(sessions.at(-1), a.now).running} />
      </>
    ),
    input: <TaskInput item={item} s={at} current={current} />,
    output: <TaskOutput item={item} s={at} docs={a.docs} onDoc={a.onDoc} produces={produces} onProduced={() => produces && a.onProduced(produces, path)} />,
    log: <Log key={at.id} sessionId={at.id} running={look.running === true} title={task} crumb={crumbs.map((c) => c.label).join(" › ")} />,
    config: <><TaskConfig path={path} s={at} /><AppliedRows applied={a.applied} path={path} /></>,
  };
  const live = sessions.some((s) => ["running", "pending"].includes(s.status));
  return {
    ...head,
    // One attempt has nothing to pick between: the subtitle stays the words it was.
    sub: sessions.length > 1 && !scopes ? <>{lead} · {menu} · {state}</> : head.sub,
    tabs,
    body: bodies[tab],
    footer: esc
      // The escalation's footer: stop it while it runs (GAP §2 #13); retry the node with a steer once it answered (#12).
      ? <PathFooter item={item} path={node.id} what="node" state={live ? null : footerState(item, item.worker_sessions.filter((s) => s.node_id === node.id && !isEscalation(s)))} reload={a.reload}
          extra={live ? <button type="button" className="btn btn-secondary" onClick={async () => { const r = await act.stopEscalation(item.id); if (r.ok) a.reload(); }}>Stop escalation</button> : undefined} />
      // `/retry` and `/skip` parse the path against the chain, which has no step on a gate (422): the reviewer has no footer.
      : rev ? undefined
      // `/retry` and `/skip` have no path under a fix loop either (422): its tasks retry the node that owns them.
      : loop ? footerOf(item, node.id, "node", footerState(item, item.worker_sessions.filter((s) => s.node_id === node.id && !isEscalation(s))), a.reload)
      : footerOf(item, path, "task", footerState(item, sessions), a.reload),
  };
}
