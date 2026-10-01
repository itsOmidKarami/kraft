import { describe, expect, it } from "vitest";
import { libView, PUBLISHED } from "./fixture";
import { listRows, shownRows } from "./rows";
import type { Section } from "./types";

const result = libView().result;
const rows = (r = result) => listRows(r, PUBLISHED.components);

describe("library rows", () => {
  it("groups by section in the list's order, names in file order", () => {
    expect(rows().map((x) => x.id)).toEqual([
      "nodes.verification", "nodes.approval",
      "steps.checks",
      "tasks.implementer", "tasks.code_review", "tasks.verify", "tasks.await_ci",
      "steering.project-standards", "steering.never-signal-processes-you-didnt-start",
    ]);
  });

  it("says who uses it from the published list: N chains, unused, unpublished, and nothing until it arrives", () => {
    const by = Object.fromEntries(rows().map((x) => [x.id, x.used]));
    expect(by["tasks.implementer"]).toBe("2 chains");
    expect(by["tasks.code_review"]).toBe("1 chain");
    expect(by["tasks.verify"]).toBe("unused");
    // In the draft but not in the published library: new.
    expect(by["nodes.approval"]).toBe("unpublished");
    expect(listRows(result, null).every((x) => x.used === "")).toBe(true);
  });

  it("marks an added or changed component, and one a problem names", () => {
    const r = libView({
      changes: [{ path: "nodes.approval", kind: "add", summary: "added" }, { path: "tasks.implementer.prompt", kind: "change", summary: "prompt" }],
      problems: [{ path: "tasks.verify", field: "command", message: "Field required", file: "library.yaml", line: 3, col: 1 }, { path: "implementation.main.implement", field: null, message: "unknown", file: "chains/default.yaml", line: 1, col: 1, chain: "default", component: "tasks.await_ci" }],
    }).result;
    const by = Object.fromEntries(rows(r).map((x) => [x.id, x]));
    expect(by["nodes.approval"].mark).toBe("add");
    expect(by["tasks.implementer"].mark).toBe("change");
    expect(by["steps.checks"].mark).toBeUndefined();
    expect(by["tasks.verify"].problem).toBe(true);
    expect(by["tasks.await_ci"].problem).toBe(true);
    expect(by["tasks.implementer"].problem).toBe(false);
  });

  it("draws a task by its own kind, else its base's, and a gate as a gate", () => {
    const by = Object.fromEntries(rows().map((x) => [x.id, x.glyph]));
    expect(by["tasks.implementer"].taskKind).toBe("agent");
    expect(by["tasks.code_review"].taskKind).toBe("agent");
    expect(by["tasks.await_ci"].taskKind).toBe("forge");
    expect(by["nodes.approval"].gate).toBe(true);
    expect(by["steps.checks"].icon).toBe("layers");
  });

  it("filters by search and by hidden kinds", () => {
    const all = rows();
    expect(shownRows(all, "REVIEW", new Set()).map((x) => x.id)).toEqual(["tasks.code_review"]);
    const hide = new Set<Section>(["tasks", "steering"]);
    expect(shownRows(all, "", hide).map((x) => x.section)).toEqual(["nodes", "nodes", "steps"]);
  });
});
