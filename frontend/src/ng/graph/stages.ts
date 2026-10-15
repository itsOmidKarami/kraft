import { displayName } from "../board/rowText";
import type { ChainNode } from "./layout";

/** A run of nodes a gate ends; the nodes after the last gate are the final stage, with no gate. */
export type Stage = { key: string; gate?: ChainNode; nodes: ChainNode[] };

/** A stage drawn as one pill on the node view's strip: by its gate in words (the final stage by its last node), passed or not. */
export type Fold = { kind: "fold"; key: string; words: string; nodes: ChainNode[]; passed: boolean };
export type RowEntry = ChainNode | Fold;

/** The chain cut after every gate. A stage is keyed by its gate, the final one by its last node. */
export function stagesOf(nodes: ChainNode[]): Stage[] {
  const out: Stage[] = [];
  let run: ChainNode[] = [];
  for (const n of nodes) {
    run.push(n);
    if (n.kind === "gate") {
      out.push({ key: n.id, gate: n, nodes: run });
      run = [];
    }
  }
  if (run.length) out.push({ key: run[run.length - 1].id, nodes: run });
  return out;
}

/** The stage the run is in: the first with a node not done (running, waiting, stopped or not started), or the last of
 *  a chain that finished. */
export const currentStage = (stages: Stage[]) => {
  const i = stages.findIndex((s) => s.nodes.some((n) => n.state !== "done"));
  return i < 0 ? stages.length - 1 : i;
};


/** A run's name: the gate that ends it, or the last run's last node. */
export const runWords = (s: Stage) => displayName(s.gate ?? s.nodes[s.nodes.length - 1]);

const count = (n: number) => `${n} ${n === 1 ? "node" : "nodes"}`;

/** A folded pill's accessible name on the node view's strip: "approve spec, 2 nodes, passed: expand". */
export const foldName = (f: Fold) => `${f.words}, ${count(f.nodes.length)}${f.passed ? ", passed" : ""}: expand`;

/** The row a node view's strip draws: the stage holding the viewed node (and any in `open`) node by node, every other
 *  stage a pill. A stage is passed once every node in it is done. */
export function stripRow(nodes: ChainNode[], open: ReadonlySet<string>, viewing: string): RowEntry[] {
  return stagesOf(nodes).flatMap((s): RowEntry[] => {
    if (open.has(s.key) || s.nodes.some((n) => n.id === viewing)) return s.nodes;
    return [{ kind: "fold", key: s.key, words: runWords(s), nodes: s.nodes, passed: s.nodes.every((n) => n.state === "done") }];
  });
}
