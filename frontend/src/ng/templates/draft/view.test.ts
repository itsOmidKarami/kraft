import { describe, expect, it } from "vitest";
import { DEFAULT_VIEW } from "./fixture.default";
import type { Result } from "./types";
import { authoredAt, authoredNodes, changeAt, counts, kindOf, normalise, problemsAt, resolvedAt, resolvedNode, sourceRows, sourceWord, valueAt } from "./view";

const R = DEFAULT_VIEW.result;
const withR = (extra: Partial<Result>): Result => ({ ...R, ...extra });

describe("draft view", () => {
  it("reads the nodes from the authored model, kinds from the resolved chain for an extends", () => {
    const nodes = authoredNodes(R, "default");
    expect(nodes.map((n) => n.id)).toEqual(["spec", "spec_approval", "implementation", "verification", "local_review", "merge_request_feedback"]);
    const impl = nodes.find((n) => n.id === "implementation")!;
    expect(impl.kind).toBeUndefined();
    expect(kindOf(R, impl)).toBe("exec");
    expect(kindOf(R, nodes[1])).toBe("gate");
  });

  it("normalises the tasks shorthand to one step main, and leaves steps alone", () => {
    expect(normalise({ id: "n", tasks: [{ id: "t" }] })).toEqual({ id: "n", steps: [{ id: "main", tasks: [{ id: "t" }] }] });
    expect(normalise({ id: "n", steps: [{ id: "a", tasks: [] }] })!.steps.map((s) => s.id)).toEqual(["a"]);
    expect(normalise({ id: "g" })!.steps).toEqual([]);
  });

  it("gives a node's resolved steps, inherited ones included", () => {
    expect(resolvedNode(R, "verification")!.steps!.map((s) => s.id)).toEqual(["tests", "review"]);
    expect(resolvedNode(R, "implementation")!.steps![0].tasks.map((t) => t.id)).toEqual(["implement"]);
    expect(resolvedNode(withR({ resolved: null }), "verification")).toBeNull();
  });

  it("finds problems at a path, and a node's children's when deep", () => {
    const r = withR({ problems: [{ path: "verification.review.code_review", field: "model", message: "m", file: "f", line: 3, col: 1 }] });
    expect(problemsAt(r, "verification")).toEqual([]);
    expect(problemsAt(r, "verification", true)).toHaveLength(1);
    expect(problemsAt(r, "verification.review.code_review")).toHaveLength(1);
    expect(problemsAt(r, "verification_x", true)).toEqual([]);
  });

  it("marks a path by its own change, else an added or removed ancestor's, never a changed one's", () => {
    const r = withR({ changes: [{ path: "lint", kind: "add", summary: "added" }, { path: "verification", kind: "change", summary: "moved" }] });
    expect(changeAt(r, "lint")?.kind).toBe("add");
    expect(changeAt(r, "lint.main.t")?.kind).toBe("add");
    expect(changeAt(r, "verification")?.kind).toBe("change");
    expect(changeAt(r, "verification.tests")).toBeUndefined();
  });

  it("lists Config rows in the server's order, without id, kind and icon", () => {
    const rows = sourceRows(R, "implementation.main.implement");
    const fields = rows.map((x) => x.field);
    expect(fields.slice(0, 4)).toEqual(["scope", "steering", "skippable", "harness"]);
    expect(fields).not.toContain("id");
    expect(fields).not.toContain("icon");
    expect(rows.find((x) => x.field === "profile")).toEqual({ field: "profile", value: "strong", source: "library:tasks.implementer" });
  });

  it("counts a YAML error as a problem", () => {
    expect(counts(R)).toEqual({ changes: 0, problems: 0 });
    expect(counts(withR({ yaml_error: { file: "f", line: 1, col: 1, message: "x" } })).problems).toBe(1);
  });

  it("words a source the way the chip shows it", () => {
    expect(["chain", "library:tasks.implementer", "policy", "default"].map(sourceWord)).toEqual(["this chain", "library", "policy", "default"]);
  });
});

describe("walk", () => {
  it("finds a component by canonical path: shorthand main steps, steps, the fix loop and its judge", () => {
    const r = DEFAULT_VIEW.result;
    expect(authoredAt(r, "default", "spec.main.author")).toEqual({ id: "author", extends: "spec_author" });
    expect(resolvedAt(r, "verification.review.code_review")?.kind).toBe("agent");
    expect(resolvedAt(r, "verification.fix_loop.judge")?.id).toBe("judge");
    expect(resolvedAt(r, "verification.fix_loop.main.repair")?.profile).toBe("strong");
    expect(resolvedAt(r, "verification.nope")).toBeNull();
    expect(authoredAt(r, "default", "")?.id).toBe("default");
  });

  it("reads a value from the sources, else from the model", () => {
    const r = DEFAULT_VIEW.result;
    expect(valueAt(r, "default", "implementation.main.implement", "profile")).toBe("strong");
    expect(valueAt({ ...r, sources: {}, resolved: null }, "default", "spec_approval", "message")).toBe("Review and approve the specification.");
  });
});
