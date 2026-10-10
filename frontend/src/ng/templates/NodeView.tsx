import { useContext, useRef, useState } from "react";
import { ChainStrip } from "../graph/ChainStrip";
import { GateView } from "../graph/GateView";
import type { ChainNode } from "../graph/layout";
import { NodeGraph, type NodeSel } from "../graph/NodeGraph";
import type { NodeStep } from "../graph/nodeLayout";
import type { GraphItem } from "../graph/types";
import type { TaskKind } from "../icons";
import { showToast } from "../ui/Toast";
import type { ConfigDraft } from "./draft/useConfigDraft";
import type { Op, Scope } from "./draft/types";
import { authoredAt, authoredNodes, changeAt, kindOf, nodeGlyph, normalise, problemsAt, resolvedAt, valueAt, type NodeA, type Step } from "./draft/view";
import { ExtendMenu } from "./menus/ExtendMenu";
import { IdCard } from "./menus/IdCard";
import { TaskMenu, type TaskChoice } from "./menus/TaskMenu";
import { ReadOnly } from "./plugin";
import { problemWord } from "./problems";
import type { TSel } from "./sel";

type Menu =
  | { t: "task"; step: string | null; at?: number; title: string }
  | { t: "slot"; slot: "auto_review"; title: string }
  | { t: "extend" }
  | { t: "first"; step: string };

/** A step id the server would also pick: `step_<n>`, made unique (the prototype's `uniq`). */
export function nextStepId(ids: string[]): string {
  let n = ids.length + 1;
  // The server renames a lone `main` to step_1 before adding (W9 add_step).
  const taken = ids.length === 1 && ids[0] === "main" ? ["step_1"] : ids;
  while (taken.includes(`step_${n}`)) n++;
  return `step_${n}`;
}

const TASK_KINDS = new Set(["agent", "builtin", "subprocess", "forge"]);

/** The node view (Decisions §9): the strip, then the node's steps on the canvas
 *  with seams and slots; an empty node's two phrases; a gate's GateView. */
export function NodeView({ scope, node, libStep, draft, selected, reserve, onPick, onOpen, onEscape, onBackground, onBack, onFocusNode }: {
  scope: Scope;
  /** The node's canonical path: a chain's node id, or the library's `nodes.<name>` (or `steps` with `libStep`). */
  node: string;
  /** The Library's step `steps.<libStep>`, drawn as the one step of a node called `steps`. */
  libStep?: string;
  draft: ConfigDraft;
  selected: TSel;
  reserve: number;
  onPick: (path: string) => void;
  onOpen: (path: string) => void;
  onEscape: () => void;
  onBackground: () => void;
  onBack: () => void;
  onFocusNode: (id: string) => void;
}) {
  const r = draft.view!.result;
  const lib = scope.area === "library";
  const nodes = authoredNodes(r, scope);
  // The library draft has no resolved chain: a component is drawn as it is written (R18).
  const own = (lib ? (libStep ? ({ ...authoredAt(r, scope, `steps.${libStep}`), id: libStep } as NodeA) : authoredAt(r, scope, node)) : nodes.find((n) => n.id === node)) as NodeA | undefined | null;
  const [menu, setMenu] = useState<Menu | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const readOnly = useContext(ReadOnly);
  const anchor = useRef<HTMLElement | null>(null);
  const open = (m: Menu, el: HTMLElement | null) => {
    anchor.current = el;
    setRefused(null);
    setMenu(m);
  };
  const close = () => {
    setMenu(null);
    anchor.current?.focus?.();
  };

  /** Refusals show on the id card when one is open (`quiet`), else as the page's toast. */
  const send = async (ops: Op[], quiet = false) => {
    const a = await draft.ops(ops, { quiet });
    if (a.status !== 200) setRefused(String((a.body as { detail?: string }).detail ?? "refused"));
    return a.status === 200;
  };

  const strip: ChainNode[] = nodes.map((n) => ({ id: n.id, kind: kindOf(r, n), icon: typeof n.icon === "string" ? n.icon : nodeGlyph(r, n.id).icon, taskKind: nodeGlyph(r, n.id).taskKind, prob: problemsAt(r, n.id, true).length > 0 }));
  const top = lib ? null : <ChainStrip nodes={strip} viewing={node} onOpen={onFocusNode} onBack={onBack} />;
  const areaClass = lib ? "tpl-node-area no-strip" : "tpl-node-area";
  if (!own) return <>{top}<div className="tpl-note">There is no {lib ? "component" : "node"} called {node} in {lib ? "the library" : scope.key}.</div></>;

  if (kindOf(r, own) === "gate") {
    const rev = resolvedAt(r, `${node}.auto_review`) ?? authoredAt(r, scope, `${node}.auto_review`);
    const g = resolvedAt(r, node) ?? own;
    const target = typeof g.reject_to === "string" ? g.reject_to : undefined;
    const revPath = `${node}.auto_review`;
    return (
      <>
        {top}
        <div className={areaClass}>
          <GateView
            gate={{ id: node, mark: markOf(changeAt(r, node)?.kind), prob: problemsAt(r, node).length > 0, sel: selected.kind === "node" }}
            reviewer={rev ? { id: String(rev.id ?? "reviewer"), icon: typeof rev.icon === "string" ? rev.icon : undefined, sel: (selected as TSel).path === revPath, prob: problemsAt(r, revPath).length > 0, mark: markOf(changeAt(r, revPath)?.kind) } : undefined}
            message={typeof g.message === "string" ? g.message : undefined}
            doc={typeof g.artifact === "string" ? { label: g.artifact, onClick: () => onOpen(node) } : undefined}
            reject={target ? { id: target, onClick: () => onFocusNode(target) } : undefined}
            right={reserve}
            onAdd={rev || readOnly ? undefined : () => open({ t: "slot", slot: "auto_review", title: "Add a reviewer" }, document.querySelector<HTMLElement>(".gateview-item.is-add"))}
            onReviewer={() => onOpen(revPath)}
            onGate={() => onOpen(node)}
            onBackground={onBackground}
          />
        </div>
        {menu?.t === "slot" && <TaskMenu anchor={anchor} title={menu.title} agentOnly onClose={close} onPick={(c) => addSlot(c)} />}
      </>
    );
  }

  const steps: Step[] = lib ? (libStep ? [{ id: libStep, tasks: ((own as NodeA).tasks as Step["tasks"] | undefined) ?? [] } as Step] : normalise(own)?.steps ?? []) : draft.resolvedNode(node)?.steps ?? [];
  const ids = steps.map((s) => s.id);
  const lone = steps.length === 1 && steps[0].id === "main";
  const busy = new Set((draft.pending ?? []).map((o) => String(o.path ?? o.container ?? "")));
  const graphSteps: NodeStep[] = steps.map((s) => {
    const sp = `${node}.${s.id}`;
    return {
      id: s.id,
      label: lone ? " " : s.id,
      mark: markOf(changeAt(r, sp)?.kind),
      prob: problemsAt(r, sp).length > 0,
      // A library step is one step: nothing before or after it, but tasks may run beside its own.
      seamBefore: !libStep && !readOnly,
      seamBelow: s.tasks.length > 0 && !readOnly,
      slot: s.tasks.length || readOnly ? undefined : { label: "add a task" },
      tasks: s.tasks.map((t): GraphItem => {
        const tp = `${sp}.${t.id}`;
        const probs = problemsAt(r, tp);
        const kind = String(t.kind ?? valueAt(r, scope, tp, "kind") ?? "");
        return {
          id: t.id,
          icon: typeof t.icon === "string" ? t.icon : undefined,
          taskKind: TASK_KINDS.has(kind) ? (kind as TaskKind) : undefined,
          mark: markOf(changeAt(r, tp)?.kind),
          prob: probs.length > 0,
          meta: probs.length ? problemWord(probs[0]) : undefined,
          metaTone: probs.length ? "red" : undefined,
          pending: busy.has(tp) || busy.has(sp),
        };
      }),
    };
  });

  // A task into a step, or a new step with it in one request (one undo). The
  // ids are the ones the server would pick, sent so the page can select it.
  const addTask = async (c: TaskChoice) => {
    if (!menu || menu.t !== "task") return;
    const step = menu.step ?? nextStepId(ids);
    const taken = steps.find((s) => s.id === step)?.tasks.map((x) => x.id) ?? [];
    const id = uniq("kind" in c ? c.kind : c.extends.split(":").pop()!, taken);
    const ops: Op[] = menu.step ? [] : [{ op: "add_step", container: node, at: menu.at ?? ids.length, id: step }];
    ops.push({ op: "add_task", container: node, step, id, ...c });
    if (!(await send(ops))) return;
    setMenu(null);
    onOpen(`${node}.${step}.${id}`);
  };
  async function addSlot(c: TaskChoice) {
    if (!(await send([{ op: "add_task", slot: "auto_review", node, ...c }]))) return;
    setMenu(null);
    onOpen(`${node}.auto_review`);
  }

  const firstStep = async (el: HTMLElement) => {
    anchor.current = el;
    if (!(await send([{ op: "add_step", container: node, at: 0, id: "step_1" }]))) return;
    onPick(`${node}.step_1`);
    // The card sits on the new step's name once it is drawn.
    requestAnimationFrame(() => open({ t: "first", step: "step_1" }, document.querySelector<HTMLElement>(`[aria-label="step_1, step"]`) ?? el));
  };

  const sel: NodeSel | undefined = selected.kind === "task" ? { step: selected.step, task: selected.task } : selected.kind === "step" ? { step: selected.step } : undefined;
  const pathOfSel = (s: NodeSel) => `${node}.${s.step}${s.task ? `.${s.task}` : ""}`;
  const extend = typeof own.extends === "string" ? own.extends : null;

  return (
    <>
      {top}
      <div className={areaClass}>
        {steps.length ? (
          <NodeGraph
            name={node}
            steps={graphSteps}
            selected={sel}
            seamAfter={!libStep && !readOnly}
            reserve={reserve}
            onSelect={(s) => onPick(pathOfSel(s))}
            onOpen={(s) => onOpen(pathOfSel(s))}
            onExpand={(s) => onOpen(pathOfSel(s))}
            onEscape={onEscape}
            onBackground={onBackground}
            onSlot={(step, el) => open({ t: "task", step, title: "Add a task" }, el)}
            onSeam={(where, at, el) => open(where === "below" ? { t: "task", step: String(at), title: "Add a parallel task" } : { t: "task", step: null, at: Number(at), title: "Add a step" }, el)}
          />
        ) : extend ? (
          <div className="tpl-empty-node"><p>Extends <code>{extend}</code>. {lib ? <><button type="button" className="tpl-phrase" onClick={() => onFocusNode(extend)}>Open it</button> to see its steps.</> : "Its steps show once the draft resolves."}</p></div>
        ) : readOnly ? (
          <div className="tpl-empty-node"><p>This node is empty.</p></div>
        ) : (
          // Decisions §9 New exec node: the two phrases are the actions.
          <div className="tpl-empty-node" onKeyDown={(e) => e.key === "Escape" && onEscape()}>
            <p>
              This node is empty,{" "}
              <button type="button" className="tpl-phrase" onClick={(e) => void firstStep(e.currentTarget)}>add your first step</button>
              , or{" "}
              <button type="button" className="tpl-phrase" onClick={(e) => open({ t: "extend" }, e.currentTarget)}>extend a node from the library</button>
            </p>
          </div>
        )}
        {extend && steps.length > 0 && <span className="tpl-extends-pill">extends {extend}</span>}
      </div>
      {menu?.t === "task" && <TaskMenu anchor={anchor} title={menu.title} onClose={close} onPick={addTask} />}
      {menu?.t === "extend" && (
        <ExtendMenu anchor={anchor} exclude={lib ? node.split(".")[1] : undefined} note={lib ? "Edits you make afterwards override it." : undefined} onClose={close} onPick={async (base) => {
          const a = await draft.ops([{ op: "extend", node, base }]);
          if (a.status !== 200) return;
          setMenu(null);
          onOpen(node);
          const dropped = a.body.ops?.[0]?.result?.dropped as string[] | undefined;
          showToast(`Base is now ${base}${dropped?.length ? ` · dropped ${lib ? "its" : "this chain's"} ${dropped.join(", ")}` : ""}`);
        }} />
      )}
      {menu?.t === "first" && (
        <IdCard
          anchor={anchor}
          title="Name the first step"
          initial={menu.step}
          taken={[]}
          go="Next →"
          refused={refused}
          note="Enter keeps it and opens the task menu."
          onClose={close}
          onGo={async (id) => {
            if (id !== menu.step) {
              if (!(await send([{ op: "rename", path: `${node}.${menu.step}`, id }], true))) return;
              onPick(`${node}.${id}`);
            }
            // Then the task menu under the step's slot (Decisions §9 First step).
            requestAnimationFrame(() => open({ t: "task", step: id, title: "Add a task" }, document.querySelector<HTMLElement>(".graph-node.is-slot") ?? anchor.current));
          }}
        />
      )}
    </>
  );
}

/** `base`, else `base_2`, `base_3`… (W9's default ids). */
export const uniq = (base: string, taken: string[]) => {
  if (!taken.includes(base)) return base;
  let i = 2;
  while (taken.includes(`${base}_${i}`)) i++;
  return `${base}_${i}`;
};

const markOf = (k?: string) => (k === "add" ? "add" : k === "change" ? "change" : undefined);
