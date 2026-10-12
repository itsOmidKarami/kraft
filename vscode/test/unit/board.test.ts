import { describe, expect, it, vi } from "vitest";
import * as vscode from "vscode";
import { Board, registerBoard } from "../../src/board";
import type { Store } from "../../src/core/store";
import { announceable, contextValueOf, describe as describeItem, groupHolding, groupItems, iconOf, needsYouCount, steerPrompt, tooltipOf, welcomeState, worktreeTarget } from "../../src/core/board";
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
  expect(groupItems([it_], () => true).map((g) => g.id)).toEqual([group]);
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
    const groups = groupItems(items, () => true);
    expect(groups.map((g) => [g.id, g.label])).toEqual([["needs", "Needs you"], ["running", "Running"], ["not_started", "Not started"], ["done", "Done"]]);
    expect(groups.flatMap((g) => g.items.map((i) => i.id))).not.toContain("arch");
    expect(groups.flatMap((g) => g.items.map((i) => i.id))).not.toContain("arch-needs");
    expect(groups.find((g) => g.id === "not_started")!.items.map((i) => i.id)).toEqual(["new", "after"]);
  });

  it("scopes to the workspace's repos", () => {
    const ids = groupItems(items, (i) => i.repo === "/r").flatMap((g) => g.items.map((i) => i.id));
    expect(ids).not.toContain("other");
    expect(ids).toContain("run");
  });

  it("omits empty groups", () => {
    expect(groupItems([item()], () => true).map((g) => g.id)).toEqual(["running"]);
  });
});

describe("groupHolding", () => {
  const items = [item({ id: "run" }), item({ id: "new", display_status: "paused", current_node_id: null }),
    item({ id: "arch", display_status: "archived", archived_at: "x" }), item({ id: "other", repo: "/elsewhere" })];
  const here = (i: { repo: string }) => i.repo === "/r";
  it.each([
    ["a running item, under Running", "run", "running"],
    ["a just-filed paused item, under Not started", "new", "not_started"],
    ["nothing for an archived item", "arch", undefined],
    ["nothing for an item outside the scope", "other", undefined],
    ["nothing for an unknown id", "nope", undefined],
  ])("%s", (_, id, want) => expect(groupHolding(items, here, id)?.id).toBe(want));
});

describe("contextValueOf", () => {
  it.each([
    ["the server's actions, in its order", item({ actions: ["pause", "cancel"] }), "item:pause,cancel"],
    ["gate first while one is pending", stopped("gate", { pending_gate: "spec", actions: ["reject", "retry"] }), "item:gate,reject,retry"],
    ["no actions from an older server", item(), "item:"],
    ["worktree last while it is on disk", item({ actions: ["pause", "cancel"], worktree_exists: true }), "item:pause,cancel,worktree"],
    ["no worktree once it was reclaimed", item({ actions: ["pause", "cancel"], worktree_exists: false }), "item:pause,cancel"],
    ["a gate with its worktree", stopped("gate", { pending_gate: "spec", actions: ["reject"], worktree_exists: true }), "item:gate,reject,worktree"],
  ])("%s", (_, it_, want) => {
    expect(contextValueOf(it_)).toBe(want);
  });

  it.each([
    ["drafts after the actions, before the worktree", { drafts: true }, "item:reject,drafts,worktree"],
    ["no drafts token without drafts", { drafts: false }, "item:reject,worktree"],
  ])("%s", (_, opts, want) => expect(contextValueOf(item({ actions: ["reject"], worktree_exists: true }), opts)).toBe(want));

  it("an action that merely starts with drafts is not the drafts token", () =>
    expect(contextValueOf(item({ actions: ["drafts_x" as never] }))).not.toMatch(/\bdrafts\b/));

  it("a menu keyed on one action matches no other", () => {
    const value = contextValueOf(stopped("budget", { actions: ["raise_budget"] }));
    expect(value).not.toMatch(/\bbudget\b/);
    expect(value).toMatch(/\braise_budget\b/);
  });
});

const counted = [item(), item({ display_status: "paused" }), stopped("gate", { pending_gate: "g" }), stopped("cap", { display_status: "archived", archived_at: "x" }), archivedNeedsYou, stopped("cap", { id: "away", repo: "/elsewhere" })];

it.each([
  ["everything in scope", () => true, 3],
  ["only what is in scope", (i: { repo: string }) => i.repo === "/r", 2],
])("counts what needs you: %s", (_, include, want) => {
  expect(needsYouCount(counted, include)).toBe(want);
});

it("announces only what is in scope", () => {
  const items = [stopped("gate", { id: "here", pending_gate: "g" }), stopped("gate", { id: "away", repo: "/elsewhere", pending_gate: "g" })];
  expect(announceable(items, (i) => i.repo === "/r").map((i) => i.id)).toEqual(["here"]);
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

it.each([
  ["not connected", false, true, false, 0, "none"],
  ["connected, scope not ready yet", true, false, false, 0, "none"],
  ["something visible", true, true, false, 2, "none"],
  ["connected, ready, nothing in this repo", true, true, false, 0, "here"],
  ["showing every repo, nothing anywhere", true, true, true, 0, "none"],
])("welcome: %s", (_, connected, ready, showAll, visible, want) => {
  expect(welcomeState(connected, ready, showAll, visible)).toBe(want);
});


it.each([
  ["exists, with a path", item({ worktree_exists: true, worktree_path: "/k/run/worktrees/K-1" }), "/k/run/worktrees/K-1"],
  ["reclaimed", item({ worktree_exists: false, worktree_path: "/k/run/worktrees/K-1" }), undefined],
  ["exists, no path", item({ worktree_exists: true }), undefined],
  ["exists, empty path", item({ worktree_exists: true, worktree_path: "" }), undefined],
  ["no detail", undefined, undefined],
])("worktree target: %s", (_, detail, want) => {
  expect(worktreeTarget(detail)).toBe(want);
});

describe("Board, revealing an item", () => {
  const items = [item({ id: "run" }), item({ id: "other", repo: "/elsewhere" })];
  const store = { items: () => items, item: (id: string) => items.find((i) => i.id === id), detail: () => undefined,
    onChange: () => () => {}, onConnection: () => () => {}, onError: () => () => {}, connected: true } as unknown as Store;
  const scope = { onChange: () => () => {}, ready: true, showAll: false };
  const here = (i: { repo: string }) => i.repo === "/r";
  const board = new Board(store, here, scope);

  it.each([
    ["an item's parent is the group holding it", { kind: "item", item: items[0] }, "running"],
    ["an item the board does not show has none", { kind: "item", item: items[1] }, undefined],
    ["a group has none", { kind: "group", id: "running", label: "Running", items }, undefined],
  ] as const)("%s", (_, node, want) => expect((board.getParent(node as never) as { id?: string } | undefined)?.id).toBe(want));

  it("names a group row by its key, so it keeps its identity as its count changes", () =>
    expect(board.getTreeItem({ kind: "group", id: "running", label: "Running", items } as never).id).toBe("group:running"));

  it("selects a shown item without focus, passes over one it does not show, and swallows a miss", async () => {
    const reveal = vi.fn().mockRejectedValue(new Error("not resolved yet"));
    vi.spyOn(vscode.window, "createTreeView").mockReturnValue({ reveal, dispose() {} } as never);
    const { reveal: show } = registerBoard({ subscriptions: [] } as never, store, {} as never, () => null, here, scope);
    await expect(show("run")).resolves.toBeUndefined();
    await show("other");
    expect(reveal.mock.calls).toEqual([[{ kind: "item", item: items[0] }, { select: true, focus: false }]]);
  });
});
