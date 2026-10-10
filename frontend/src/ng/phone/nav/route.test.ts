import { describe, expect, it } from "vitest";
import { backLabel, parentOf, screenKey, tabOf } from "./route";

describe("parentOf", () => {
  it("has no parent on a tab's own page", () => {
    for (const r of ["/", "/search", "/analytics", "/more"]) expect(parentOf(r)).toBeNull();
  });

  it("walks the work item's stack: task, node, item, board", () => {
    expect(parentOf("/work-items/a/nodes/verification?sel=verification.review.code_review&tab=log&attempt=2")).toBe("/work-items/a/nodes/verification");
    // A scope is a screen over its task, and the fix-loop round is every one of theirs.
    expect(parentOf("/work-items/a/nodes/verification?sel=verification.tests.scopes&round=1&scope=%3Ajust+test&tab=log")).toBe("/work-items/a/nodes/verification?sel=verification.tests.scopes&round=1");
    expect(parentOf("/work-items/a/nodes/verification?sel=verification.tests.scopes&round=1")).toBe("/work-items/a/nodes/verification?round=1");
    expect(parentOf("/work-items/a/nodes/verification?tab=log")).toBe("/work-items/a");
    expect(parentOf("/work-items/a")).toBe("/");
    expect(parentOf("/work-items/a/review?gate=final_review")).toBe("/work-items/a");
    expect(parentOf("/work-items/new")).toBe("/");
  });

  it("puts a document or the YAML view over its own screen", () => {
    expect(parentOf("/work-items/a/nodes/n?sel=n.s.t&doc=spec")).toBe("/work-items/a/nodes/n?sel=n.s.t");
    // R10b-10: an attachment read before start sits over its item, as a document does.
    expect(parentOf("/work-items/a?attached=spec")).toBe("/work-items/a");
    expect(parentOf("/settings/access?yaml=1")).toBe("/settings/access");
  });

  it("walks the areas back to More", () => {
    expect(parentOf("/templates/chains")).toBe("/more");
    expect(parentOf("/templates/chains/default")).toBe("/templates/chains");
    expect(parentOf("/templates/chains/default/nodes/verification")).toBe("/templates/chains/default");
    expect(parentOf("/templates/library/implementation")).toBe("/templates/library");
    expect(parentOf("/settings/harnesses/profiles/strong")).toBe("/settings/harnesses");
    expect(parentOf("/settings/auto-intake/schedules/2")).toBe("/settings/auto-intake");
    expect(parentOf("/settings/auto-intake")).toBe("/more");
    expect(parentOf("/settings/policy/loops")).toBe("/more");
    expect(parentOf("/settings/about")).toBe("/more");
  });

  it("puts the archived list under More, where its row is", () => {
    expect(parentOf("/archived")).toBe("/more");
    expect(parentOf("/archived?x=1")).toBe("/more");
    expect(backLabel("/archived")).toBe("More");
  });
});

describe("backLabel", () => {
  it("names the parent", () => {
    expect(backLabel("/work-items/a")).toBe("Board");
    expect(backLabel("/work-items/a/nodes/n")).toBe("Chain");
    expect(backLabel("/work-items/a/nodes/n?sel=n.s.t")).toBe("Node");
    expect(backLabel("/work-items/a/nodes/n?sel=n.s.t&scope=%3Ajust+test")).toBe("Task");
    expect(backLabel("/work-items/a/review")).toBe("Back");
    expect(backLabel("/work-items/a?doc=x")).toBe("Back");
    expect(backLabel("/work-items/a?attached=spec")).toBe("Back");
    expect(backLabel("/templates/chains")).toBe("More");
    expect(backLabel("/templates/chains/default")).toBe("Chains");
  });
});

describe("tabOf", () => {
  it("shows the bar on the four roots and the areas, hides it elsewhere", () => {
    expect(tabOf("/")).toBe("board");
    expect(tabOf("/search")).toBe("search");
    expect(tabOf("/settings/repos/kraft")).toBe("more");
    expect(tabOf("/settings/access")).toBe("more");
    expect(tabOf("/archived")).toBe("more");
    for (const r of ["/work-items/a", "/work-items/a/nodes/n", "/work-items/a/review", "/work-items/new"]) expect(tabOf(r)).toBeNull();
  });
});

describe("screenKey", () => {
  it("tells a task from its node by sel, and ignores the rest of the query", () => {
    expect(screenKey("/work-items/a/nodes/n?tab=log")).toBe(screenKey("/work-items/a/nodes/n"));
    expect(screenKey("/work-items/a/nodes/n?sel=n.s.t")).not.toBe(screenKey("/work-items/a/nodes/n"));
    expect(screenKey("/work-items/a/nodes/n?sel=n.s.t&scope=%3Ajust+test")).not.toBe(screenKey("/work-items/a/nodes/n?sel=n.s.t"));
  });
});
