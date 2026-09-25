import { describe, expect, it } from "vitest";
import { actionsFor, describe as describeItem, groupItems, needsYouCount } from "../../src/core/board";

const item = (id: string, extra: Record<string, unknown> = {}) =>
  ({ id, title: id, repo: "/r", status: "active", current_node_id: "impl", chain_definition: { nodes: [] }, ...extra }) as any;

describe("groupItems", () => {
  const items = [
    item("run"),
    item("gate", { status: "needs_human", pending_gate: "spec" }),
    item("new", { status: "paused", current_node_id: null }),
    item("done", { status: "completed" }),
    item("arch", { status: "completed", archived_at: "2026-09-01" }),
    item("other", { repo: "/elsewhere" }),
  ];

  it("groups in the web UI's order and hides archived items", () => {
    const groups = groupItems(items, "all");
    expect(groups.map((g) => g.id)).toEqual(["needs", "running", "not_started", "done"]);
    expect(groups.flatMap((g) => g.items.map((i) => i.id))).not.toContain("arch");
  });

  it("scopes to the workspace's repos", () => {
    const ids = groupItems(items, ["/r"]).flatMap((g) => g.items.map((i) => i.id));
    expect(ids).not.toContain("other");
    expect(ids).toContain("run");
  });

  it("omits empty groups", () => {
    expect(groupItems([item("run")], "all").map((g) => g.id)).toEqual(["running"]);
  });
});

describe("actionsFor", () => {
  it.each([
    [{ status: "active" }, ["pause", "cancel"]],
    [{ status: "paused" }, ["resume", "cancel"]],
    [{ status: "paused", current_node_id: null }, ["resume", "cancel"]],
    [{ status: "needs_human", pending_gate: "spec" }, ["skip", "cancel"]],
    [{ status: "needs_human" }, ["retry", "skip", "escalate", "cancel"]],
    [{ status: "completed" }, ["archive"]],
    [{ status: "completed", archived_at: "x" }, []],
  ])("%o → %o", (extra, want) => {
    expect(actionsFor(item("x", extra))).toEqual(want);
  });
});

it("counts what needs you", () => {
  expect(needsYouCount([item("a"), item("b", { status: "paused" }), item("c", { status: "needs_human", pending_gate: "g" })])).toBe(2);
});

it("describes an item", () => {
  expect(describeItem(item("K-1", { progress: { current: 3, total: 7 } }))).toBe("K-1 · impl · Task 3 of 7");
  expect(describeItem(item("K-2", { current_node_id: null }))).toBe("K-2");
});
