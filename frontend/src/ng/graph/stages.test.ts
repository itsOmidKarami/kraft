import { describe, expect, it } from "vitest";
import type { ChainNode } from "./layout";
import { foldName, foldStages, rowSeams, stagesOf, type Fold, type RowEntry } from "./stages";

/** The shipped default chain's shape, at a run that has passed the first `done` nodes and stands on the next (`at`). */
const IDS: [string, "exec" | "gate"][] = [
  ["spec", "exec"], ["spec_approval", "gate"], ["plan", "exec"], ["plan_approval", "gate"], ["chain_revision", "exec"], ["chain_revision_approval", "gate"],
  ["implementation", "exec"], ["verification", "exec"], ["work_brief", "exec"], ["local_review", "gate"],
  ["describe_merge_request", "exec"], ["draft_merge_request", "exec"], ["merge_request_feedback", "exec"], ["work_item_summary", "exec"], ["final_review", "gate"],
  ["mark_ready", "exec"], ["external_approval", "exec"], ["merge", "exec"], ["post_merge_ci", "exec"],
];
const chain = (done: number, at: ChainNode["state"] = "amber"): ChainNode[] =>
  IDS.map(([id, kind], i) => ({ id, kind, state: i < done ? "done" : i === done ? at : "todo" }));
/** A row as words: a node by its id, a pill as `[words ✓ n]` passed or `[words · n]` ahead. */
const show = (row: RowEntry[]) => row.map((e) => (e.kind === "fold" ? `[${e.words}${e.passed ? " ✓" : " ·"} ${e.nodes.length}]` : e.id)).join(" ");

describe("stages", () => {
  it("cuts the chain after every gate; the nodes after the last gate are the final stage, keyed by its last node", () => {
    expect(stagesOf(chain(0)).map((s) => [s.key, s.gate?.id, s.nodes.length])).toEqual([
      ["spec_approval", "spec_approval", 2], ["plan_approval", "plan_approval", 2], ["chain_revision_approval", "chain_revision_approval", 2],
      ["local_review", "local_review", 4], ["final_review", "final_review", 5], ["post_merge_ci", undefined, 4],
    ]);
    expect(stagesOf([{ id: "a", kind: "exec" }, { id: "b", kind: "exec" }]).map((s) => s.key)).toEqual(["b"]);
    expect(stagesOf([])).toEqual([]);
  });

  it.each([
    ["at the first gate: it and the next stage open, the rest ahead folded", chain(1), "spec spec_approval plan plan_approval [chain revision · 2] [local review · 4] [final review · 5] [post merge ci · 4]"],
    ["at a later gate: the passed stages fold, done", chain(9), "[spec ✓ 2] [plan ✓ 2] [chain revision ✓ 2] implementation verification work_brief local_review describe_merge_request draft_merge_request merge_request_feedback work_item_summary final_review [post merge ci · 4]"],
    ["running mid-stage, the stage it runs in stays open", chain(7, "current"), "[spec ✓ 2] [plan ✓ 2] [chain revision ✓ 2] implementation verification work_brief local_review describe_merge_request draft_merge_request merge_request_feedback work_item_summary final_review [post merge ci · 4]"],
    ["stopped on a node: that node's stage is where it stands", chain(4, "failed"), "[spec ✓ 2] [plan ✓ 2] chain_revision chain_revision_approval implementation verification work_brief local_review [final review · 5] [post merge ci · 4]"],
    ["finished: everything but the last stage folds", chain(19), "[spec ✓ 2] [plan ✓ 2] [chain revision ✓ 2] [local review ✓ 4] [final review ✓ 5] mark_ready external_approval merge post_merge_ci"],
    ["not started: the first two stages open", chain(-1), "spec spec_approval plan plan_approval [chain revision · 2] [local review · 4] [final review · 5] [post merge ci · 4]"],
  ])("folds a stage %s", (_, nodes, row) => {
    expect(show(foldStages(nodes, new Set()))).toBe(row);
  });

  it("keeps open a stage opened by hand, or holding the selected node", () => {
    expect(show(foldStages(chain(9), new Set(["plan_approval"])))).toMatch(/^\[spec ✓ 2\] plan plan_approval \[chain revision ✓ 2\] /);
    expect(show(foldStages(chain(9), new Set(), "merge"))).toMatch(/ final_review mark_ready external_approval merge post_merge_ci$/);
  });

  it("keeps no stage past the current one open when asked (a draft's row)", () => {
    expect(show(foldStages(chain(-1), new Set(), undefined, 0))).toBe("spec spec_approval [plan · 2] [chain revision · 2] [local review · 4] [final review · 5] [post merge ci · 4]");
  });

  it("names a pill by its gate, its size and whether it passed", () => {
    const [passed] = foldStages(chain(9), new Set()) as Fold[];
    expect(foldName(passed)).toBe("spec stage, 2 nodes, passed: expand");
    const last = foldStages(chain(1), new Set()).at(-1) as Fold;
    expect(foldName(last)).toBe("post merge ci stage, 4 nodes, not started: expand");
    expect(foldName({ ...last, nodes: last.nodes.slice(0, 1) })).toBe("post merge ci stage, 1 node, not started: expand");
  });

  it("moves seams onto the folded row, dropping those inside a pill", () => {
    const nodes = chain(9);
    const row = foldStages(nodes, new Set());
    // 2: between spec_approval and plan (two pills); 1: inside spec's pill; 10: after local_review; 16: inside post_merge_ci's; 19: the end.
    expect(rowSeams(nodes, row, [{ at: 1 }, { at: 2 }, { at: 10 }, { at: 16 }, { at: 19 }])).toEqual([
      { at: 1, node: 2 }, { at: 7, node: 10 }, { at: 13, node: 19 },
    ]);
    expect(rowSeams(nodes, nodes, [{ at: 0 }, { at: 4 }])).toEqual([{ at: 0, node: 0 }, { at: 4, node: 4 }]);
  });
});
