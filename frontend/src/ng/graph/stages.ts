import { gateWords, nodeWords } from "../board/rowText";
import type { ChainNode } from "./layout";

/** A run of nodes a gate ends; the nodes after the last gate are the final stage, with no gate. */
export type Stage = { key: string; gate?: ChainNode; nodes: ChainNode[] };

/** A stage drawn as one pill: by its gate in words (the final stage by its last node), passed or still ahead. */
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

/** The row an item's chain draws: a stage the run has passed folds to a pill, and so does every stage after the next
 *  one, which reads as what is still to come; the current stage and the next stay node by node. A stage in `open`, or
 *  holding the selected node, stays open too. `ahead` is how many stages after the current one stay open (an item's: 1;
 *  a draft, which has run nothing, 0). */
export function foldStages(nodes: ChainNode[], open: ReadonlySet<string>, selected?: string, ahead = 1): RowEntry[] {
  const stages = stagesOf(nodes);
  const cur = currentStage(stages);
  return stages.flatMap((s, i): RowEntry[] => {
    if ((i >= cur && i <= cur + ahead) || open.has(s.key) || s.nodes.some((n) => n.id === selected)) return s.nodes;
    const words = s.gate ? gateWords(s.gate.id) : nodeWords(s.key);
    return [{ kind: "fold", key: s.key, words, nodes: s.nodes, passed: i < cur }];
  });
}

/** A pill's accessible name: "spec stage, 2 nodes, passed: expand". */
export const foldName = (f: Fold) => `${f.words} stage, ${f.nodes.length} ${f.nodes.length === 1 ? "node" : "nodes"}, ${f.passed ? "passed" : "not started"}: expand`;

/** Seams (`at`: before node `at` of the chain) moved onto a folded row: `at` becomes a row position, and a seam
 *  inside a folded stage is not drawn until the stage opens. `node` keeps the chain position the seam stands for. */
export function rowSeams<S extends { at: number }>(nodes: ChainNode[], row: RowEntry[], seams: S[]): (S & { node: number })[] {
  const entry = new Map<string, number>();
  row.forEach((e, k) => (e.kind === "fold" ? e.nodes : [e]).forEach((n) => entry.set(n.id, k)));
  return seams.flatMap((s) => {
    const a = s.at > 0 ? entry.get(nodes[s.at - 1].id) : -1;
    const b = s.at < nodes.length ? entry.get(nodes[s.at].id)! : row.length;
    return a === b ? [] : [{ ...s, at: b, node: s.at }];
  });
}
