import { describe, expect, it } from "vitest";
import { actionsFor, describe as describeItem, groupItems, iconOf, needsYouCount, stateOf, steerPrompt, tooltipOf, type Action, type ItemState } from "../../src/core/board";
import { item, stopped } from "./fixtures/item";

const archivedNeedsYou = stopped("cap", { id: "arch-needs", archived_at: "x" });

it.each([
  ["gate", stopped("gate", { pending_gate: "spec_approval" }), "pass", "needs"],
  ["question", stopped("question"), "question", "needs"],
  ["budget", stopped("budget"), "credit-card", "needs"],
  ["cap", stopped("cap"), "error", "needs"],
  ["conflict", stopped("conflict"), "warning", "needs"],
  ["failed", item({ display_status: "failed", stop: { kind: "failed", node: "implementation", reason: "x", resume_at: null } }), "error", "needs"],
  ["paused", item({ display_status: "paused" }), "debug-pause", "needs"],
  ["not started", item({ display_status: "paused", current_node_id: null }), "debug-pause", "not_started"],
  ["running", item(), "sync~spin", "running"],
  ["waiting", item({ display_status: "waiting" }), "watch", "running"],
  ["queued", item({ display_status: "queued" }), "watch", "running"],
  ["blocked", item({ display_status: "blocked", current_node_id: null }), "watch", "not_started"],
  ["escalated", item({ display_status: "escalated" }), "comment-discussion", "running"],
  ["done", item({ display_status: "done" }), "check", "done"],
  ["cancelled", item({ display_status: "cancelled", status: "abandoned" }), "circle-slash", "done"],
  ["newer server", item({ display_status: "someday" as never }), "circle-filled", "running"],
  ["pre-2.0 server", item({ display_status: undefined }), "circle-filled", "running"],
])("%s: icon and group", (_, it_, icon, group) => {
  expect(iconOf(it_)).toBe(icon);
  expect(groupItems([it_], "all").map((g) => g.id)).toEqual([group]);
});

describe("groupItems", () => {
  const items = [
    item({ id: "run" }),
    stopped("gate", { id: "gate", pending_gate: "spec" }),
    item({ id: "new", display_status: "paused", current_node_id: null }),
    item({ id: "after", display_status: "blocked", current_node_id: null }),
    item({ id: "done", display_status: "done" }),
    item({ id: "arch", display_status: "archived", archived_at: "x" }),
    archivedNeedsYou,
    item({ id: "other", repo: "/elsewhere" }),
  ];

  it("groups in the web board's order and hides archived items", () => {
    const groups = groupItems(items, "all");
    expect(groups.map((g) => [g.id, g.label])).toEqual([["needs", "Needs you"], ["running", "Running"], ["not_started", "Not started"], ["done", "Done"]]);
    expect(groups.flatMap((g) => g.items.map((i) => i.id))).not.toContain("arch");
    expect(groups.flatMap((g) => g.items.map((i) => i.id))).not.toContain("arch-needs");
    expect(groups.find((g) => g.id === "not_started")!.items.map((i) => i.id)).toEqual(["new", "after"]);
  });

  it("scopes to the workspace's repos", () => {
    const ids = groupItems(items, ["/r"]).flatMap((g) => g.items.map((i) => i.id));
    expect(ids).not.toContain("other");
    expect(ids).toContain("run");
  });

  it("omits empty groups", () => {
    expect(groupItems([item()], "all").map((g) => g.id)).toEqual(["running"]);
  });
});

describe("actionsFor", () => {
  const OF: Record<ItemState, ReturnType<typeof item>> = {
    gate: stopped("gate", { pending_gate: "spec" }),
    question: stopped("question"),
    budget: stopped("budget"),
    cap: stopped("cap"),
    needs_you: stopped("mr_closed"),
    failed: item({ display_status: "failed" }),
    paused: item({ display_status: "paused" }),
    not_started: item({ display_status: "paused", current_node_id: null }),
    running: item(),
    waiting: item({ display_status: "waiting" }),
    queued: item({ display_status: "queued" }),
    blocked: item({ display_status: "blocked" }),
    escalated: item({ display_status: "escalated" }),
    done: item({ display_status: "done" }),
    cancelled: item({ display_status: "cancelled" }),
    archived: item({ display_status: "archived", archived_at: "x" }),
    unknown: item({ display_status: "someday" as never }),
  };
  const WANT: [ItemState, Action[]][] = [
    ["running", ["pause", "cancel"]],
    ["waiting", ["pause", "cancel"]],
    ["queued", ["pause", "cancel"]],
    ["blocked", ["pause", "cancel"]],
    ["escalated", ["cancel"]],
    ["paused", ["resume", "cancel"]],
    ["not_started", ["resume", "cancel"]],
    ["gate", ["skip", "cancel"]],
    ["question", ["retry", "skip", "escalate", "cancel"]],
    ["cap", ["retry", "skip", "escalate", "cancel"]],
    ["needs_you", ["retry", "skip", "escalate", "cancel"]],
    ["failed", ["retry", "skip", "escalate", "cancel"]],
    ["budget", ["retry", "skip", "cancel"]],
    ["done", ["archive"]],
    ["cancelled", ["archive"]],
    ["archived", []],
    ["unknown", []],
  ];

  it.each(WANT)("%s → %o", (state, want) => {
    expect(stateOf(OF[state])).toBe(state);
    expect(actionsFor(OF[state])).toEqual(want);
  });

  it("an archived row has no actions, whatever its display status", () => {
    expect(stateOf(archivedNeedsYou)).toBe("archived");
    expect(actionsFor(archivedNeedsYou)).toEqual([]);
  });

  it("covers every state", () => {
    expect(WANT.map(([s]) => s).sort()).toEqual(Object.keys(OF).sort());
  });
});

it("counts what needs you", () => {
  expect(needsYouCount([item(), item({ display_status: "paused" }), stopped("gate", { pending_gate: "g" }), stopped("cap", { display_status: "archived", archived_at: "x" }), archivedNeedsYou])).toBe(2);
});

it.each([
  ["a question", stopped("question", { stop: { kind: "question", node: "implementation", reason: "needs_context: Keep the header?", resume_at: null } }), "K-1 · agent asks: Keep the header?"],
  ["a stop with no reason", stopped("cap"), "K-1 · waiting for you at implementation"],
  ["a row with no display status", item({ display_status: undefined }), "K-1"],
])("describes %s", (_, it_, want) => {
  expect(describeItem(it_)).toBe(want);
});

it.each([
  ["a question, without its prefix", stopped("question", { stop: { kind: "question", node: "implementation", reason: "needs_context: Keep the header?", resume_at: null } }), "Keep the header?"],
  ["a cap's whole reason", stopped("cap", { stop: { kind: "cap", node: "implementation", reason: "Loop cap hit. Raise it to retry.", resume_at: null } }), "Loop cap hit. Raise it to retry."],
  ["a stop with no reason", stopped("cap"), "K-1 — waiting for you at implementation"],
  ["a running row", item(), "K-1 — implementation"],
  ["a row with no tail", item({ display_status: "someday" as never }), "K-1 — someday"],
  ["a row with no display status", item({ display_status: undefined }), "K-1 — active"],
])("tooltip: %s", (_, it_, want) => {
  expect(tooltipOf(it_)).toBe(want);
});

it.each([
  ["a question", stopped("question", { stop: { kind: "question", node: "implementation", reason: "needs_context: Keep the header?", resume_at: null } }), "K-1 asks: Keep the header? Your answer steers it (optional)"],
  ["a question with no reason", stopped("question"), "Steer for K-1 (optional)"],
  ["a cap", stopped("cap", { stop: { kind: "cap", node: "implementation", reason: "cap hit", resume_at: null } }), "Steer for K-1 (optional)"],
])("steer prompt for %s", (_, it_, want) => {
  expect(steerPrompt(it_)).toBe(want);
});
