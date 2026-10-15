import { describe, expect, it } from "vitest";
import type { ChainNode } from "./layout";
import { foldName, stagesOf, stripRow, type Fold } from "./stages";

/** The shipped default chain's shape, at a run that has passed the first `done` nodes and stands on the next (`at`). */
const IDS: [string, "exec" | "gate"][] = [
  ["spec", "exec"], ["spec_approval", "gate"], ["plan", "exec"], ["plan_approval", "gate"], ["chain_revision", "exec"], ["chain_revision_approval", "gate"],
  ["implementation", "exec"], ["verification", "exec"], ["work_brief", "exec"], ["local_review", "gate"],
  ["describe_merge_request", "exec"], ["draft_merge_request", "exec"], ["merge_request_feedback", "exec"], ["work_item_summary", "exec"], ["final_review", "gate"],
  ["mark_ready", "exec"], ["external_approval", "exec"], ["merge", "exec"], ["post_merge_ci", "exec"],
];
const chain = (done: number, at: ChainNode["state"] = "amber"): ChainNode[] =>
  IDS.map(([id, kind], i) => ({ id, kind, state: i < done ? "done" : i === done ? at : "todo" }));

describe("stages", () => {
  it("cuts the chain after every gate; the nodes after the last gate are the final stage, keyed by its last node", () => {
    expect(stagesOf(chain(0)).map((s) => [s.key, s.gate?.id, s.nodes.length])).toEqual([
      ["spec_approval", "spec_approval", 2], ["plan_approval", "plan_approval", 2], ["chain_revision_approval", "chain_revision_approval", 2],
      ["local_review", "local_review", 4], ["final_review", "final_review", 5], ["post_merge_ci", undefined, 4],
    ]);
    expect(stagesOf([{ id: "a", kind: "exec" }, { id: "b", kind: "exec" }]).map((s) => s.key)).toEqual(["b"]);
    expect(stagesOf([])).toEqual([]);
  });

  it("names a folded pill on the node view's strip", () => {
    const [passed] = stripRow(chain(9), new Set(), "implementation") as Fold[];
    expect(foldName(passed)).toBe("approve spec, 2 nodes, passed: expand");
    expect(foldName({ ...passed, passed: false, nodes: passed.nodes.slice(0, 1) })).toBe("approve spec, 1 node: expand");
  });
});
