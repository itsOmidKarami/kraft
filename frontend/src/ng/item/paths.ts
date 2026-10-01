import type { ChainNode } from "../../types";

/** A step of a node: its id and its tasks' canonical paths. */
export interface Step {
  id: string;
  tasks: string[];
}

/** The node's steps from the frozen chain. A V1 node's `steps` hold canonical
 *  task paths `node.step.task`, so a step's id is the second segment. A legacy
 *  node (plain hook ids, no dots) gets steps numbered 1…n; `legacy` says so,
 *  and the item then retries and skips only at node level (lifecycle's path
 *  grammar does not know its tasks). */
export function stepsOf(node: ChainNode): { steps: Step[]; legacy: boolean } {
  const groups = node.steps ?? node.tasks.map((t) => [t]);
  const legacy = groups.flat().some((t) => t.split(".").length !== 3);
  return {
    legacy,
    steps: groups.map((tasks, i) => ({ id: legacy ? String(i + 1) : tasks[0].split(".")[1], tasks })),
  };
}

export const taskName = (path: string) => path.split(".").at(-1) ?? path;

/** The path a retry or skip may name: the task's own on a V1 node, else the node. */
export function actionPath(node: ChainNode, task?: string | null): string {
  if (!task) return node.id;
  const { legacy, steps } = stepsOf(node);
  if (legacy) return node.id;
  const full = task.includes(".") ? task : steps.flatMap((s) => s.tasks).find((t) => taskName(t) === task);
  return full ?? node.id;
}
