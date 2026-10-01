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
  it("falls back to the node for a legacy node, no task, or an unknown task", () => {
    expect(actionPath(legacy, "self_review")).toBe("implement");
    expect(actionPath(v1, null)).toBe("verification");
    expect(actionPath(v1, "nope")).toBe("verification");
  });
});
