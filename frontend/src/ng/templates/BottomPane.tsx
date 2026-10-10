import { useContext, useRef, useState, type KeyboardEvent, type PointerEvent as RPointerEvent } from "react";
import { ChevronDown, ChevronUp, GitCompare, RefreshCw, Siren, Zap } from "lucide-react";
import { NodeGraph, type NodeSel } from "../graph/NodeGraph";
import type { NodeStep } from "../graph/nodeLayout";
import type { GraphItem } from "../graph/types";
import type { TaskKind } from "../icons";
import { Button } from "../ui/Button";
import type { ConfigDraft } from "./draft/useConfigDraft";
import type { Op } from "./draft/types";
import type { Scope } from "./draft/types";
import { authoredAt, changeAt, normalise, problemsAt, valueAt, type NodeA, type Step, type Task } from "./draft/view";
import { TaskMenu, type TaskChoice } from "./menus/TaskMenu";
import { nextStepId, uniq } from "./NodeView";
import { ReadOnly } from "./plugin";
import { problemWord } from "./problems";
import { autoEscalates } from "./escalateWords";
import { tip } from "../ui/Tooltip";

export type BottomTab = "on_failure" | "fix_loop" | "escalation" | "on_conflict";
const LABEL: Record<BottomTab, string> = { on_failure: "On failure", fix_loop: "Fix loop", escalation: "Escalation", on_conflict: "On conflict" };
const ICON = { on_failure: Zap, fix_loop: RefreshCw, escalation: Siren, on_conflict: GitCompare } as const;

export const BOTTOM = { MIN: 120, RESERVE: 90, BAR: 38, STEP: 16, KEY: "kraft.ng.bottom.chains" } as const;
/** Decisions §9 Panes: from 120px to the canvas height minus 90. */
export const clampBottom = (h: number, canvas: number) => Math.max(BOTTOM.MIN, Math.min(canvas - BOTTOM.RESERVE, h));

/** The handler a canonical path sits in, if any: the bottom pane's own items. */
export function handlerOf(path: string): BottomTab | null {
  const segs = path.split(".");
  if (segs.includes("on_conflict")) return "on_conflict";
  if (segs.includes("fix_loop")) return "fix_loop";
  if (segs.includes("escalation")) return "escalation";
  if (segs.includes("on_failure")) return "on_failure";
  return null;
}

/** Decisions §9 Bottom pane: a main step or task shows only On failure; else
 *  all three, plus On conflict when "on base change" is set. */
export function tabsFor(selPath: string, node: string, hasConflict: boolean): BottomTab[] {
  const inMain = selPath.startsWith(`${node}.`) && !handlerOf(selPath);
  if (inMain) return ["on_failure"];
  return ["on_failure", "fix_loop", "escalation", ...(hasConflict ? (["on_conflict"] as BottomTab[]) : [])];
}

function load(): number | null {
  try {
    return Number(localStorage.getItem(BOTTOM.KEY)) || null;
  } catch {
    return null;
  }
}
function save(h: number) {
  try {
    localStorage.setItem(BOTTOM.KEY, String(h));
  } catch {
    // Private windows: the height just isn't remembered.
  }
}

const TASK_KINDS = new Set(["agent", "builtin", "subprocess", "forge"]);
const markOf = (k?: string) => (k === "add" ? "add" : k === "change" ? "change" : undefined);

/** The node canvas's bottom pane (Decisions §9 Bottom pane, On failure, Fix
 *  loop, Escalation, On conflict): collapsed to its tab bar on entry, docked
 *  under the canvas up to the side pane's edge. */
export function BottomPane({ scope, node, draft, selPath, tab, open, canvasH, right, onTab, onToggle, onPick, onOpen, onLeave }: {
  scope: Scope;
  /** The node's canonical path: a chain's node id, or the library's `nodes.<name>`. */
  node: string;
  draft: ConfigDraft;
  selPath: string;
  tab: BottomTab;
  open: boolean;
  canvasH: number;
  /** Px the side pane takes on the right. */
  right: number;
  onTab: (t: BottomTab) => void;
  onToggle: () => void;
  onPick: (path: string) => void;
  onOpen: (path: string) => void;
  /** Switching tabs leaves a bottom item: back to the node, collapsed. */
  onLeave: () => void;
}) {
  const r = draft.view!.result;
  const readOnly = useContext(ReadOnly);
  // The library draft has no resolved chain: its node is drawn as written (R18). `node` is a canonical path, dots and all.
  const n = (scope.area === "library" ? normalise(authoredAt(r, scope, node)) : draft.resolvedNode(node)) as (NodeA & Record<string, unknown>) | null;
  const conflict = (n?.on_base_changed as Record<string, unknown> | null | undefined) ?? null;
  const tabs = tabsFor(selPath, node, !!conflict);
  const shown = tabs.includes(tab) ? tab : tabs[0];
  const [raw, setRaw] = useState<number | null>(load);
  const height = open ? clampBottom(raw ?? Math.round(Math.min(300, Math.max(170, (canvasH - 150) * 0.42))), canvasH) : BOTTOM.BAR;
  const [level, setLevel] = useState<"task" | "step" | "node" | null>(null);
  const [menu, setMenu] = useState<{ title: string; agentOnly?: boolean; pick: (c: TaskChoice) => Op[] } | null>(null);
  const anchor = useRef<HTMLElement | null>(null);

  const resize = {
    onPointerDown: (e: RPointerEvent) => {
      if (!open) return;
      e.preventDefault();
      const y0 = e.clientY, h0 = height;
      let last = h0;
      const move = (ev: PointerEvent) => setRaw((last = clampBottom(h0 + y0 - ev.clientY, canvasH)));
      const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); save(last); };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    },
    onKeyDown: (e: KeyboardEvent) => {
      const next = { ArrowUp: height + BOTTOM.STEP, ArrowDown: height - BOTTOM.STEP, Home: BOTTOM.MIN, End: canvasH - BOTTOM.RESERVE }[e.key];
      if (next === undefined) return;
      e.preventDefault();
      const c = clampBottom(next, canvasH);
      setRaw(c);
      save(c);
    },
  };

  // The handler being shown, its container path, and what an add or a remove sends.
  const owner = (() => {
    if (shown !== "on_failure") return { path: node, label: node };
    const rel = !handlerOf(selPath) && selPath.startsWith(`${node}.`) ? selPath.slice(node.length + 1).split(".") : [];
    const ctx = { step: rel.length >= 1 && rel.length <= 2 ? rel[0] : null, task: rel.length === 2 ? rel[1] : null };
    const levels = [ctx.task && "task", ctx.step && "step", "node"].filter(Boolean) as ("task" | "step" | "node")[];
    const lv = level && levels.includes(level) ? level : levels[0];
    const path = lv === "task" ? `${node}.${ctx.step}.${ctx.task}` : lv === "step" ? `${node}.${ctx.step}` : node;
    return { path, label: lv === "node" ? node : lv === "step" ? `step ${ctx.step}` : ctx.task!, levels, lv, ctx };
  })();

  const at = (path: string): Record<string, unknown> | null => {
    if (path === node) return n;
    const [a, b] = path.slice(node.length + 1).split(".");
    const st = (n?.steps as Step[] | undefined)?.find((s) => s.id === a);
    return b ? (st?.tasks.find((t) => t.id === b) as Record<string, unknown> | undefined) ?? null : (st as Record<string, unknown> | undefined) ?? null;
  };
  const handler = shown === "on_failure" ? normalise(at(owner.path)?.on_failure as NodeA | null)
    : shown === "fix_loop" ? normalise(n?.fix_loop as NodeA | null)
      : shown === "on_conflict" ? normalise(conflict?.on_conflict as NodeA | null) : null;
  const container = shown === "on_failure" ? `${owner.path}.on_failure` : shown === "fix_loop" ? `${node}.fix_loop` : `${node}.on_base_changed.on_conflict`;
  const esc = shown === "escalation" ? (n?.escalation as Task | null | undefined) ?? null : null;
  const judge = shown === "fix_loop" ? ((n?.fix_loop as Record<string, unknown> | undefined)?.judge as Task | undefined) ?? null : null;

  const item = (path: string, t: Task): GraphItem => {
    const probs = problemsAt(r, path);
    const kind = String(t.kind ?? valueAt(r, scope, path, "kind") ?? "");
    return { id: t.id, icon: typeof t.icon === "string" ? t.icon : undefined, taskKind: TASK_KINDS.has(kind) ? (kind as TaskKind) : undefined, mark: markOf(changeAt(r, path)?.kind), prob: probs.length > 0, meta: probs.length ? problemWord(probs[0]) : undefined, metaTone: probs.length ? "red" : undefined };
  };
  const steps: NodeStep[] = (handler?.steps ?? []).map((s) => ({
    id: s.id,
    label: handler!.steps.length === 1 && s.id === "main" ? " " : s.id,
    seamBefore: shown !== "escalation" && !readOnly,
    seamBelow: s.tasks.length > 0 && !readOnly,
    slot: s.tasks.length || readOnly ? undefined : { label: "add a task" },
    tasks: s.tasks.map((t) => item(`${container}.${s.id}.${t.id}`, t)),
  }));
  const ids = steps.map((s) => s.id);
  const openMenu = (el: HTMLElement, title: string, pick: (c: TaskChoice) => Op[], agentOnly = false) => {
    anchor.current = el;
    setMenu({ title, pick, agentOnly });
  };
  const addInto = (step: string | null, at?: number) => (c: TaskChoice): Op[] => {
    const sid = step ?? nextStepId(ids);
    const taken = handler?.steps.find((s) => s.id === sid)?.tasks.map((t) => t.id) ?? [];
    const id = uniq("kind" in c ? c.kind : c.extends.split(":").pop()!, taken);
    return [...(step ? [] : [{ op: "add_step", container, at: at ?? ids.length, id: sid }]), { op: "add_task", container, step: sid, id, ...c }];
  };

  const empty = (() => {
    const delay = autoEscalates(r.policy_values?.auto_escalate_delay_s ?? 0);
    const restart = typeof conflict?.restart_from === "string" ? conflict.restart_from : "…";
    switch (shown) {
      case "on_failure": return { a: `No handler for ${owner.label}. `, link: "Add a recovery plan", b: " that runs once when it fails.", note: "Without one, the failure goes straight to the fix loop.", go: (el: HTMLElement) => void addHandler("on_failure", owner.path, el) };
      case "fix_loop": return { a: "No fix loop. ", link: "Add a fix loop", b: " that repairs and re-measures this node.", note: "Without one, a failed measurement stops the node for you.", go: (el: HTMLElement) => void addHandler("fix_loop", node, el) };
      case "escalation": return { a: "No escalation task. ", link: "Add one", b: " to run when this node is stuck.", note: `Without one, Kraft auto-escalates ${delay} (policy).`, go: (el: HTMLElement) => openMenu(el, "Add an escalation task", (c) => [{ op: "add_task", slot: "escalation", node, ...c }], true) };
      default: return { a: "No conflict handler. ", link: "Add one", b: " to resolve a rebase conflict.", note: `Without one, a rebase conflict is an ordinary task failure. Success restarts from ${restart}.`, go: (el: HTMLElement) => void addHandler("on_conflict", node, el) };
    }
  })();

  // A new handler holds one empty step `main`; the task menu then opens on it (the prototype's addHandler).
  async function addHandler(kind: "on_failure" | "fix_loop" | "on_conflict", path: string, el: HTMLElement) {
    const a = await draft.ops([{ op: "add_handler", path, kind }]);
    if (a.status !== 200) return;
    const c = kind === "on_failure" ? `${path}.on_failure` : kind === "fix_loop" ? `${node}.fix_loop` : `${node}.on_base_changed.on_conflict`;
    anchor.current = el;
    setMenu({ title: "Add a task", pick: (ch) => [{ op: "add_task", container: c, step: "main", id: "kind" in ch ? ch.kind : ch.extends, ...ch }] });
  }

  const remove = () => {
    if (shown === "escalation") return void draft.ops([{ op: "remove", path: `${node}.escalation` }]).then(onLeave);
    void draft.ops([{ op: "remove_handler", path: shown === "on_failure" ? owner.path : node, kind: shown }]).then(onLeave);
  };
  const hasRemove = !readOnly && (shown === "escalation" ? !!esc : !!handler);
  const selIn = (path: string) => selPath === path;
  const pathOf = (s: NodeSel) => `${container}.${s.step}${s.task ? `.${s.task}` : ""}`;
  const sel: NodeSel | undefined = selPath.startsWith(`${container}.`) ? (() => {
    const [step, task] = selPath.slice(container.length + 1).split(".");
    return { step, task };
  })() : undefined;
  const escPath = esc ? `${node}.escalation.${esc.id}` : "";
  const judgePath = `${node}.fix_loop.judge`;

  return (
    <section className={`bp${open ? " is-open" : ""}`} aria-label="Failure handling" style={{ right, height }}>
      {open && <div className="bp-handle" role="separator" aria-orientation="horizontal" aria-label="Resize the bottom pane" aria-valuenow={height} aria-valuemin={BOTTOM.MIN} aria-valuemax={canvasH - BOTTOM.RESERVE} tabIndex={0} {...resize} />}
      <div className="bp-bar">
        <div role="tablist" aria-label="Failure handling" className="bp-tabs">
          {tabs.map((t) => {
            const Icon = ICON[t];
            return (
              <button key={t} type="button" role="tab" aria-selected={t === shown} className={`bp-tab${t === shown ? " is-on" : ""}`} onClick={() => {
                if (!open) onToggle();
                onTab(t);
                // Picking the loop's or the escalation's tab selects it (Decisions §9 Fix loop).
                if (t === "fix_loop" && n?.fix_loop) return onPick(`${node}.fix_loop`);
                if (t === "escalation" && n?.escalation) return onPick(`${node}.escalation.${(n.escalation as Task).id}`);
                if (handlerOf(selPath)) onLeave();
              }}>
                <Icon size={13} aria-hidden />{LABEL[t]}
              </button>
            );
          })}
        </div>
        <span className="bp-gap" />
        {open && hasRemove && <Button variant="danger" onClick={remove}>{shown === "escalation" ? "Remove escalation" : shown === "fix_loop" ? "Remove fix loop" : "Remove handler"}</Button>}
        <button type="button" className="icon-btn" {...tip(open ? "Collapse the bottom pane" : "Expand the bottom pane")} aria-expanded={open} onClick={onToggle}>
          {open ? <ChevronDown size={14} aria-hidden /> : <ChevronUp size={14} aria-hidden />}
        </button>
      </div>
      {open && (
        <div className="bp-body">
          {shown === "on_failure" && owner.levels && (
            <div className="bp-path">
              Handler for
              {owner.levels.map((lv, i) => (
                <span key={lv} className="bp-path-seg">
                  {i > 0 && <span aria-hidden="true">›</span>}
                  <button type="button" aria-pressed={lv === owner.lv} className={`bp-level${lv === owner.lv ? " is-on" : ""}`} onClick={() => setLevel(lv)}>
                    {lv === "task" ? owner.ctx!.task : lv === "step" ? owner.ctx!.step : node}
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="bp-canvas">
            {shown === "escalation" ? (
              esc ? (
                <NodeGraph name="escalation" steps={[{ id: "escalation", label: " ", tasks: [{ ...item(escPath, esc), icon: typeof esc.icon === "string" ? esc.icon : "siren", state: "esc" }] }]} selected={selIn(escPath) ? { step: "escalation", task: esc.id } : undefined} onSelect={() => onPick(escPath)} onOpen={() => onOpen(escPath)} onExpand={() => onOpen(escPath)} />
              ) : null
            ) : handler ? (
              <>
                {shown === "fix_loop" && (
                  <div className="bp-judge">
                    {judge ? (
                      <button type="button" className={`bp-judge-btn${selIn(judgePath) ? " is-sel" : ""}`} onClick={() => onPick(judgePath)} onDoubleClick={() => onOpen(judgePath)}>judge · from attempt 2</button>
                    ) : readOnly ? null : (
                      <button type="button" className="bp-judge-btn is-slot" onClick={(e) => openMenu(e.currentTarget, "Add a judge", (c) => [{ op: "add_task", slot: "judge", node, ...c }], true)}>+ add a judge</button>
                    )}
                  </div>
                )}
                <NodeGraph
                  name={LABEL[shown]}
                  steps={steps}
                  selected={sel}
                  seamAfter={!readOnly}
                  onSelect={(s) => onPick(pathOf(s))}
                  onOpen={(s) => onOpen(pathOf(s))}
                  onExpand={(s) => onOpen(pathOf(s))}
                  onSlot={(step, el) => openMenu(el, "Add a task", addInto(step))}
                  onSeam={(where, at, el) => openMenu(el, where === "below" ? "Add a parallel task" : "Add a step", where === "below" ? addInto(String(at)) : addInto(null, Number(at)))}
                />
              </>
            ) : null}
            {((shown === "escalation" && !esc) || (shown !== "escalation" && !handler)) && (
              <div className="bp-empty">
                <p className="bp-empty-box">
                  {empty.a}
                  {!readOnly && <><button type="button" className="tpl-phrase" onClick={(e) => empty.go(e.currentTarget)}>{empty.link}</button>{empty.b}</>}
                </p>
                <p className="bp-empty-note">{empty.note}</p>
              </div>
            )}
          </div>
        </div>
      )}
      {menu && (
        <TaskMenu anchor={anchor} title={menu.title} agentOnly={menu.agentOnly} onClose={() => setMenu(null)} onPick={async (c) => {
          const a = await draft.ops(menu.pick(c));
          if (a.status === 200) setMenu(null);
        }} />
      )}
    </section>
  );
}
