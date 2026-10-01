import type { ChainNode } from "../../graph/layout";
import type { NodeStep } from "../../graph/nodeLayout";
import type { DraftView } from "./types";
import { issues, opsAt } from "./view";

type Marks = { view: DraftView | null; pending: string | null; own: Set<string> };
const first = (path: string | null) => path?.split(".")[0];

/** The chain canvas's nodes with the draft's marks (Decided 17): an added node is
 *  `add`, a node with ops in it `change`, a problem or passed op rings it, and the
 *  node a request is in flight for is `pending`. With no draft, the nodes as given. */
export function markNodes(nodes: ChainNode[], m: Marks): ChainNode[] {
  const view = m.view;
  if (!view || !view.ops.length) return nodes;
  const bad = issues(view);
  return nodes.map((n) => {
    const mark = !m.own.has(n.id) ? ("add" as const) : opsAt(view.ops, n.id).length ? ("change" as const) : undefined;
    const here = bad.filter((i) => i.node === n.id);
    const out: ChainNode = { ...n, ...(mark ? { mark } : {}), ...(here.length ? { prob: true } : {}), ...(first(m.pending) === n.id ? { pending: true } : {}) };
    return here.some((i) => i.passed) ? { ...out, meta: "passed", metaTone: "red" } : out;
  });
}

/** A node view's steps and tasks with the same marks, by canonical path. */
export function markSteps(steps: NodeStep[], node: string, m: Marks): NodeStep[] {
  const view = m.view;
  if (!view || !view.ops.length) return steps;
  const bad = issues(view);
  const under = (path: string) => bad.some((i) => i.path === path || i.path.startsWith(`${path}.`));
  const changed = (path: string) => opsAt(view.ops, path).length > 0;
  return steps.map((st) => {
    const sp = `${node}.${st.id}`;
    return {
      ...st,
      ...(changed(sp) ? { mark: "change" as const } : {}),
      ...(under(sp) ? { prob: true } : {}),
      tasks: st.tasks.map((t) => {
        const tp = `${sp}.${t.id}`;
        return { ...t, ...(changed(tp) ? { mark: "change" as const } : {}), ...(under(tp) ? { prob: true } : {}), ...(m.pending === tp ? { pending: true } : {}) };
      }),
    };
  });
}
