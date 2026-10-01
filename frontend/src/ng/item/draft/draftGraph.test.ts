import { describe, expect, it } from "vitest";
import type { ChainNode } from "../../graph/layout";
import type { NodeStep } from "../../graph/nodeLayout";
import { markNodes, markSteps } from "./draftGraph";
import { view } from "./fixtures";
import type { MarkedOp } from "./types";

const nodes: ChainNode[] = ["plan", "build", "scan", "ship"].map((id, i) => ({ id, kind: "exec", state: i < 2 ? "done" : "todo", meta: i < 2 ? "3m" : undefined }));
const own = new Set(["plan", "build", "ship"]);
const ov = (path: string, passed = false): MarkedOp => ({ op: "override", path, task_config: { model: "m" }, passed });
const add: MarkedOp = { op: "add_node", after: "build", node: { id: "scan", extends: "s" }, passed: false };
const m = (ops: MarkedOp[], extra: { problems?: { op: number; message: string }[]; pending?: string | null } = {}) => ({ view: view(ops, { problems: extra.problems ?? [] }), pending: extra.pending ?? null, own });

describe("markNodes", () => {
  it("returns the nodes untouched with no draft", () => {
    expect(markNodes(nodes, { view: null, pending: null, own })).toBe(nodes);
    expect(markNodes(nodes, m([]))).toBe(nodes);
  });
  it("marks an added node `add` and a node with ops inside it `change`, nothing else", () => {
    const out = markNodes(nodes, m([add, ov("ship.main.run")]));
    expect(out.map((n) => n.mark)).toEqual([undefined, undefined, "add", "change"]);
  });
  it("rings a node with a server problem, and says `passed` on one the run left", () => {
    const out = markNodes(nodes, m([ov("ship.main.run"), ov("plan.main.run", true)], { problems: [{ op: 0, message: "no" }] }));
    expect(out.map((n) => n.prob)).toEqual([true, undefined, undefined, true]);
    expect(out[0].meta).toBe("passed");
    expect(out[3].meta).toBeUndefined();
  });
  it("keeps run state and `meta` on a node with no issue", () => {
    const out = markNodes(nodes, m([ov("build.main.run")]));
    expect(out[1]).toMatchObject({ state: "done", meta: "3m", mark: "change" });
  });
  it("lights pending on the node a request is in flight for", () => {
    const out = markNodes(nodes, m([ov("ship.main.run")], { pending: "ship.main.run" }));
    expect(out.map((n) => n.pending)).toEqual([undefined, undefined, undefined, true]);
  });
});

describe("markSteps", () => {
  const steps: NodeStep[] = [{ id: "main", tasks: [{ id: "lint" }, { id: "test" }] }, { id: "review", tasks: [{ id: "code_review" }] }];
  it("marks the exact task and its step", () => {
    const out = markSteps(steps, "verify", m([ov("verify.main.lint")]));
    expect(out[0].mark).toBe("change");
    expect(out[0].tasks.map((t) => t.mark)).toEqual(["change", undefined]);
    expect(out[1].mark).toBeUndefined();
  });
  it("rings the task a problem names, and its step", () => {
    const out = markSteps(steps, "verify", m([ov("verify.review.code_review")], { problems: [{ op: 0, message: "no" }] }));
    expect(out[1].prob).toBe(true);
    expect(out[1].tasks[0].prob).toBe(true);
    expect(out[0].prob).toBeUndefined();
  });
  it("leaves another node's ops alone", () => {
    expect(markSteps(steps, "verify", m([ov("ship.main.run")]))[0].mark).toBeUndefined();
  });
});
