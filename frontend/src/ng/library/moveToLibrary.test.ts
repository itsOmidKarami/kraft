import { describe, expect, it } from "vitest";
import { movable, moveLines } from "./moveToLibrary";

describe("movable", () => {
  const task = { id: "run", kind: "subprocess", command: "make lint" };
  it("offers an exec node and a task that extend nothing", () => {
    expect(movable("lint", { id: "lint", kind: "exec" }, "node")).toEqual({ section: "nodes", word: "exec node" });
    expect(movable("lint.main.run", task, "task")).toEqual({ section: "tasks", word: "task" });
  });

  it("does not offer a gate, a step, a judge, an escalation, a reviewer, a fix loop or the chain", () => {
    for (const kind of ["gate", "step", "judge", "esc", "review", "fixloop", "chain"] as const) expect(movable("lint.main", task, kind)).toBeNull();
  });

  it("does not offer a component that already extends a library one", () => {
    expect(movable("spec.main.author", { id: "author", extends: "spec_author" }, "task")).toBeNull();
    expect(movable("verification", { id: "verification", extends: "verification" }, "node")).toBeNull();
  });

  it("does not offer what lives in a handler or slot", () => {
    for (const p of ["verification.fix_loop.main.repair", "verification.on_failure.main.cleanup", "verification.escalation.escalate", "gate.auto_review", "verification.on_base_changed.on_conflict.main.rebase", "verification.fix_loop.judge"])
      expect(movable(p, task, "task")).toBeNull();
  });

  it("offers nothing for a path with no authored component", () => {
    expect(movable("lint.main.run", null, "task")).toBeNull();
  });
});

describe("moveLines", () => {
  it("says what the library gains and what the chain keeps, from the component as written", () => {
    expect(moveLines("default", "tasks", "lint_task", "run", { id: "run", kind: "subprocess", command: "make lint" })).toEqual({
      gains: "The library gains tasks.lint_task (subprocess task, 2 keys).",
      keeps: "default keeps { id: run, extends: lint_task }.",
    });
    expect(moveLines("default", "nodes", "lint", "lint", { id: "lint", kind: "exec" }).gains).toBe("The library gains nodes.lint (exec node, 1 key).");
  });
});
