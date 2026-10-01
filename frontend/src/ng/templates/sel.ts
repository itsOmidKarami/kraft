import type { Sel } from "../graph/usePaneSelection";

/** W3's selection, plus the canonical path of what it names (W9's paths:
 *  `node`, `node.step`, `node.step.task`, `node.fix_loop.judge`…). The pane
 *  reducer floors and compares it as it does any `Sel`. */
export type TSel = Sel & { path: string };

export const CHAIN_SEL: TSel = { kind: "chain", path: "" };

/** The selection for a canonical path: a node, else a task when the resolved
 *  chain lists it as one, else a step (or a handler's container). */
export function selOf(path: string, taskPaths: string[] = []): TSel {
  if (!path) return CHAIN_SEL;
  const [node, ...rest] = path.split(".");
  if (!rest.length) return { kind: "node", node, path };
  if (taskPaths.includes(path) && rest.length > 1) return { kind: "task", node, step: rest.slice(0, -1).join("."), task: rest[rest.length - 1], path };
  return { kind: "step", node, step: rest.join("."), path };
}

export const pathOf = (s: Sel): string => (s as TSel).path ?? (s.kind === "chain" ? "" : s.kind === "node" ? s.node : s.kind === "step" ? `${s.node}.${s.step}` : `${s.node}.${s.step}.${s.task}`);
