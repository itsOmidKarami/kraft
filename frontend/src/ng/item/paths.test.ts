import { describe, expect, it } from "vitest";
import type { ChainNode } from "../../types";
import { actionPath, stepsOf } from "./paths";

const v1: ChainNode = {
  id: "verification",
  gate_after: null,
  tasks: ["verification.test.unit_tests", "verification.checks.lint", "verification.checks.typecheck"],
  steps: [["verification.test.unit_tests"], ["verification.checks.lint", "verification.checks.typecheck"]],
};
const legacy: ChainNode = { id: "implement", gate_after: null, tasks: ["implement", "self_review"] };

describe("stepsOf", () => {
  it("names a V1 step by its path's second segment", () => {
    expect(stepsOf(v1)).toEqual({
      legacy: false,
      steps: [
        { id: "test", tasks: ["verification.test.unit_tests"] },
        { id: "checks", tasks: ["verification.checks.lint", "verification.checks.typecheck"] },
      ],
    });
  });
  it("numbers a legacy node's steps, one task each", () => {
    expect(stepsOf(legacy)).toEqual({ legacy: true, steps: [{ id: "1", tasks: ["implement"] }, { id: "2", tasks: ["self_review"] }] });
  });
});

describe("actionPath", () => {
  it("is the task's canonical path on a V1 node, by full path or bare name", () => {
    expect(actionPath(v1, "verification.checks.lint")).toBe("verification.checks.lint");
    expect(actionPath(v1, "lint")).toBe("verification.checks.lint");
  });
  it.each([
    ["a legacy node", legacy, "self_review", "implement"],
    ["no task", v1, null, "verification"],
    ["an unknown task", v1, "nope", "verification"],
    // R12b-01: a stuck stop's task is the fix loop's judge, not one of the node's steps; sent as is, it was a 422.
    ["a fix-loop task, not a step of the node", v1, "verification.fix_loop.judge", "verification"],
    ["a task path of another node", v1, "merge_request.open.open_draft", "verification"],
  ] as const)("falls back to the node for %s", (_, node, task, path) => {
    expect(actionPath(node, task)).toBe(path);
  });
});
