import { describe, expect, it } from "vitest";
import { NODES, item } from "./fixtures";
import type { MarkedOp, Op } from "./types";
import { addNode, count, editable, issues, issuesAt, lines, moveAfter, opsAt, overrideOf, removeAt, seams, setField, stripPassed } from "./view";

const ov = (path: string, task_config?: Record<string, unknown>, policy?: Record<string, unknown>): Op => ({ op: "override", path, ...(task_config ? { task_config } : {}), ...(policy ? { policy } : {}) });

describe("lines and count", () => {
  it("is one line per overridden field, added node, skip and removal", () => {
    const ops: Op[] = [ov("verify.main.run", { model: "opus", effort: "high" }, { time_cap_minutes: 30 }), { op: "add_node", after: "build", node: { id: "scan", extends: "security" } }, { op: "skip", path: "ship.main.run" }, { op: "remove_node", node: "ship" }];
    expect(lines(ops).map((l) => l.text)).toEqual([
      "~ verify.main.run   model → opus",
      "~ verify.main.run   effort → high",
      "~ verify.main.run   running cap → 30m",
      "+ scan   after build · from the library (security)",
      "» skip ship.main.run",
      "- ship",
    ]);
    expect(count(ops)).toBe(6);
  });
});

describe("setField", () => {
  it("merges a second field into the op at the same path", () => {
    const a = setField([], "verify.main.run", "task_config", "model", "opus");
    const b = setField(a, "verify.main.run", "task_config", "effort", "high");
    expect(b).toEqual([ov("verify.main.run", { model: "opus", effort: "high" })]);
    expect(setField(b, "verify.main.run", "policy", "time_cap_minutes", 20)).toEqual([ov("verify.main.run", { model: "opus", effort: "high" }, { time_cap_minutes: 20 })]);
  });
  it("drops the op when its last field is cleared, and keeps the other ops", () => {
    const keep: Op = { op: "add_node", after: "build", node: { id: "scan", extends: "s" } };
    const a = setField([keep], "verify", "policy", "budget_usd", 2);
    expect(setField(a, "verify", "policy", "budget_usd", undefined)).toEqual([keep]);
  });
  it("does not leave an empty group behind", () => {
    const a = setField(setField([], "p", "task_config", "model", "m"), "p", "policy", "token_budget", 5);
    expect(setField(a, "p", "task_config", "model", undefined)).toEqual([ov("p", undefined, { token_budget: 5 })]);
  });
  it("finds the override of a path", () => {
    expect(overrideOf([ov("a", { model: "x" })], "a")).toEqual(ov("a", { model: "x" }));
    expect(overrideOf([ov("a", { model: "x" })], "b")).toBeUndefined();
  });
});

describe("op edits", () => {
  const ops: Op[] = [{ op: "add_node", after: "build", node: { id: "scan", extends: "s" } }, ov("ship", undefined, { budget_usd: 3 })];
  it("moves only an add_node's `after`", () => {
    expect(moveAfter(ops, 0, "verify")[0]).toEqual({ op: "add_node", after: "verify", node: { id: "scan", extends: "s" } });
    expect(moveAfter(ops, 1, "verify")).toEqual(ops);
  });
  it("removes the op at an index and nothing else", () => {
    expect(removeAt(ops, 0)).toEqual([ops[1]]);
    expect(addNode([], "build", "scan", "s")).toEqual([ops[0]]);
  });
  it("strips `passed` before a list is sent", () => {
    const marked: MarkedOp[] = [{ op: "skip", path: "a.b.c", passed: true }];
    expect(stripPassed(marked)).toEqual([{ op: "skip", path: "a.b.c" }]);
  });
});

describe("opsAt", () => {
  const ops: Op[] = [ov("verify.main.run", { model: "x" }), ov("verify", undefined, { budget_usd: 1 }), { op: "add_node", after: "build", node: { id: "scan" } }, ov("ship", undefined, { budget_usd: 1 })];
  it("takes a node's ops, its steps' and tasks', and its added node", () => {
    expect(opsAt(ops, "verify").map((x) => x.index)).toEqual([0, 1]);
    expect(opsAt(ops, "scan").map((x) => x.index)).toEqual([2]);
    expect(opsAt(ops, "verify.main.run").map((x) => x.index)).toEqual([0]);
    expect(opsAt(ops, "verify.main")).toHaveLength(1);
  });
});

describe("issues", () => {
  const ops: MarkedOp[] = [{ op: "override", path: "build.main.run", task_config: { model: "x" }, passed: true }, { op: "add_node", after: "plan", node: { id: "scan" }, passed: true }, { op: "override", path: "ship", policy: { budget_usd: 1 }, passed: false }];
  it("lists server problems and passed ops by op index, never recomputing the flag", () => {
    const got = issues({ ops, problems: [{ op: 2, message: "over the maximum" }] });
    expect(got).toEqual([
      { index: 0, node: "build", path: "build.main.run", message: "The run has passed this point.", passed: true },
      { index: 1, node: "scan", path: "scan", message: "The run has passed this point.", passed: true },
      { index: 2, node: "ship", path: "ship", message: "over the maximum", passed: false },
    ]);
    expect(issuesAt({ ops, problems: [] }, "build")).toHaveLength(1);
  });
});

describe("editable and seams", () => {
  it("allows only nodes after the current one", () => {
    const i = item("build");
    expect(NODES.map((n) => editable(i, NODES, n.id))).toEqual([false, false, true, true]);
  });
  it("allows every node before the item starts", () => {
    expect(NODES.map((n) => editable(item(null), NODES, n.id))).toEqual([true, true, true, true]);
  });
  it("allows nothing on an ended item or once the run is past the chain", () => {
    expect(editable(item("build", "done"), NODES, "ship")).toBe(false);
    expect(editable(item("elsewhere"), NODES, "ship")).toBe(false);
    expect(seams(item("build", "cancelled"), NODES)).toEqual([]);
  });
  it("puts seams after the current node and every later one, never before it", () => {
    const at = (i: string | null) => seams(item(i), NODES).map((s) => s.at);
    expect(at("build")).toEqual([2, 3, 4]);
    expect(at("ship")).toEqual([4]);
    expect(at(null)).toEqual([1, 2, 3, 4]);
  });
});
