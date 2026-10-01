import { describe, expect, it } from "vitest";
import { ownRows } from "./config";
import { libCrumbs, libDescribe } from "./describe";
import { libView } from "./fixture";

const r = libView().result;
const kind = (path: string) => libDescribe(r, path)?.kind;

describe("libDescribe", () => {
  it("names each library path's kind", () => {
    expect(kind("tasks.implementer")).toBe("task");
    expect(kind("steps.checks")).toBe("step");
    expect(kind("steps.checks.lint")).toBe("task");
    expect(kind("nodes.verification")).toBe("node");
    expect(kind("nodes.approval")).toBe("gate");
    expect(kind("nodes.verification.review")).toBe("step");
    expect(kind("nodes.verification.review.code_review")).toBe("task");
    expect(kind("nodes.verification.fix_loop")).toBe("fixloop");
    expect(kind("nodes.verification.fix_loop.judge")).toBe("judge");
    expect(kind("nodes.verification.fix_loop.main.repair")).toBe("task");
    expect(kind("nodes.verification.on_failure")).toBe("step");
    expect(kind("nodes.verification.on_failure.main")).toBe("step");
    expect(kind("nodes.verification.escalation.escalate")).toBe("esc");
    expect(kind("nodes.approval.auto_review")).toBe("review");
    expect(kind("steering.project-standards")).toBe("steering");
    expect(libDescribe(r, "chains.x")).toBeNull();
    expect(libDescribe(r, "tasks")).toBeNull();
  });

  it("says which component a path is in and what lies inside it", () => {
    expect(libDescribe(r, "nodes.verification.review.code_review")).toMatchObject({ component: "nodes.verification", section: "nodes", id: "code_review", inside: "review.code_review" });
    expect(libDescribe(r, "tasks.implementer")).toMatchObject({ component: "tasks.implementer", inside: "" });
  });

  it("crumbs the component and each container above the selection", () => {
    expect(libCrumbs("tasks.implementer")).toEqual([{ label: "tasks", path: "" }]);
    expect(libCrumbs("nodes.verification.review.code_review")).toEqual([{ label: "verification", path: "nodes.verification" }, { label: "review", path: "nodes.verification.review" }]);
  });
});

describe("ownRows", () => {
  it("lists what a component writes as settings, a mapping key by key, never its structure", () => {
    expect(ownRows({ id: "x", kind: "agent", extends: "y", icon: "bot", prompt: "p", steps: [], policy: { max_attempts: 2, token_budget: 5 }, on_base_changed: { restart_from: "spec" }, wait: { polling: { initial_interval: "30s" } } }).map((x) => [x.field, x.value])).toEqual([
      ["prompt", "p"],
      ["policy.max_attempts", 2],
      ["policy.token_budget", 5],
      ["on_base_changed", { restart_from: "spec" }],
      ["wait.polling.initial_interval", "30s"],
    ]);
    expect(ownRows(null)).toEqual([]);
  });
});
