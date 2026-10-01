import type { ChainNode } from "../../../types";
import type { Seam } from "../../graph/layout";
import type { DraftView, MarkedOp, Op, OverrideOp } from "./types";

const ENDED = new Set(["completed", "abandoned"]);
type ItemLike = { status: string; current_node_id: string | null };
type Group = "task_config" | "policy";

/** The node an op acts on: the new node's id, the removed node, or the path's first segment. */
export const nodeOf = (op: Op): string => (op.op === "add_node" ? op.node.id : op.op === "remove_node" ? op.node : op.path.split(".")[0]);

export type Line = { index: number; text: string; tone: "add" | "change" | "remove" | "skip" };
const val = (v: unknown) => (Array.isArray(v) ? v.join(", ") : typeof v === "string" ? v : JSON.stringify(v));

/** What Review lists: one line per added node, per overridden field, per skip or removal (Decided 3). */
export function lines(ops: Op[]): Line[] {
  return ops.flatMap((op, index): Line[] => {
    if (op.op === "add_node") return [{ index, tone: "add", text: `+ ${op.node.id}   after ${op.after}${op.node.extends ? ` · from the library (${op.node.extends})` : ""}` }];
    if (op.op === "remove_node") return [{ index, tone: "remove", text: `- ${op.node}` }];
    if (op.op === "skip") return [{ index, tone: "skip", text: `» skip ${op.path}` }];
    const fields = [...Object.entries(op.task_config ?? {}).map(([k, v]) => [k, v] as const), ...Object.entries(op.policy ?? {}).map(([k, v]) => [`policy.${k}`, v] as const)];
    return fields.map(([k, v]): Line => ({ index, tone: "change", text: `~ ${op.path}   ${k} → ${val(v)}` }));
  });
}
export const count = (ops: Op[]) => lines(ops).length;

/** The ops that act at `path`: a node's own, or those at or under a step or task path. */
export function opsAt(ops: Op[], path: string): { op: Op; index: number }[] {
  const depth = path.split(".").length;
  return ops.flatMap((op, index) => {
    const own = op.op === "add_node" || op.op === "remove_node" ? depth === 1 && nodeOf(op) === path : op.path === path || op.path.startsWith(`${path}.`);
    return own ? [{ op, index }] : [];
  });
}

/** The override op at exactly `path`, for a row to read its set fields from. */
export const overrideOf = (ops: Op[], path: string): OverrideOp | undefined => ops.find((o): o is OverrideOp => o.op === "override" && o.path === path);

/** Set one field of the override at `path` (one op per path, fields merged); `undefined` removes it, and an emptied op goes. */
export function setField(ops: Op[], path: string, group: Group, field: string, value: unknown): Op[] {
  const at = ops.findIndex((o) => o.op === "override" && o.path === path);
  const old = at < 0 ? undefined : (ops[at] as OverrideOp);
  const next: Record<string, unknown> = { ...(old?.[group] ?? {}) };
  if (value === undefined) delete next[field];
  else next[field] = value;
  const other: Group = group === "policy" ? "task_config" : "policy";
  const op: OverrideOp = { op: "override", path, ...(old?.[other] ? { [other]: old[other] } : {}), ...(Object.keys(next).length ? { [group]: next } : {}) };
  const empty = !op.task_config && !op.policy;
  if (at < 0) return empty ? ops : [...ops, op];
  return empty ? ops.filter((_, i) => i !== at) : ops.map((o, i) => (i === at ? op : o));
}

export const addNode = (ops: Op[], after: string, id: string, base: string): Op[] => [...ops, { op: "add_node", after, node: { id, extends: base } }];
export const moveAfter = (ops: Op[], index: number, after: string): Op[] => ops.map((o, i) => (i === index && o.op === "add_node" ? { ...o, after } : o));
export const removeAt = (ops: Op[], index: number): Op[] => ops.filter((_, i) => i !== index);
export const stripPassed = (ops: (Op | MarkedOp)[]): Op[] => ops.map((o) => {
  const { passed: _passed, ...rest } = o as MarkedOp;
  return rest as Op;
});

/** Something the person has to deal with before applying: a server problem, or an op the run has passed. */
export type Issue = { index: number; node: string; message: string; passed: boolean };
export const PASSED = "The run has passed this point.";

export function issues(view: Pick<DraftView, "ops" | "problems">): Issue[] {
  const out: Issue[] = view.problems.map((p) => ({ index: p.op, node: nodeOf(view.ops[p.op]), message: p.message, passed: false }));
  view.ops.forEach((o, index) => { if (o.passed) out.push({ index, node: nodeOf(o), message: PASSED, passed: true }); });
  return out.sort((a, b) => a.index - b.index);
}
export const issuesAt = (view: Pick<DraftView, "ops" | "problems">, node: string) => issues(view).filter((i) => i.node === node);

const idx = (nodes: ChainNode[], id: string | null) => (id == null ? -1 : nodes.findIndex((n) => n.id === id));

/** The index of the node the run stands on: -1 before it starts, null when it is past the chain or the item ended. */
function standing(item: ItemLike, nodes: ChainNode[]): number | null {
  if (ENDED.has(item.status)) return null;
  if (item.current_node_id == null) return -1;
  const i = idx(nodes, item.current_node_id);
  return i < 0 ? null : i;
}

/** Whether an override may be started on `nodeId` (Decided 4): only nodes after the one the run stands on. */
export function editable(item: ItemLike, nodes: ChainNode[], nodeId: string): boolean {
  const cur = standing(item, nodes);
  const at = idx(nodes, nodeId);
  return cur !== null && at > cur;
}

/** `+` seams after the current node, every later one included (Decided 6): `at` is the node index the seam sits before. */
export function seams(item: ItemLike, nodes: ChainNode[]): Seam[] {
  const cur = standing(item, nodes);
  if (cur === null) return [];
  const from = Math.max(cur + 1, 1);
  return Array.from({ length: Math.max(0, nodes.length - from + 1) }, (_, k) => ({ at: from + k }));
}
