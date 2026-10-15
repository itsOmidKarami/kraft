import { afterEach, describe, expect, it, vi } from "vitest";
import * as vscode from "vscode";
import { Board, registerBoard } from "../../src/board";
import { ApiError, type BulkResult } from "../../src/core/api";
import type { Store } from "../../src/core/store";
import { announceable, archivedChildren, colorOf, contextValueOf, groupLabel, itemLabel, hoverFromCache, describe as describeItem, groupHolding, groupItems, iconOf, needsYouCount, nextTick, steerPrompt, tooltipOf, welcomeState, withArchived, worktreeTarget } from "../../src/core/board";
import { log } from "../../src/log";
import { item, stopped } from "./fixtures/item";

const archivedNeedsYou = stopped("cap", { id: "arch-needs", archived_at: "x" });

it.each([
  ["gate", stopped("gate", { pending_gate: "spec_approval" }), "pass", "needs"],
  ["question", stopped("question"), "question", "needs"],
  ["budget", stopped("budget"), "credit-card", "needs"],
  ["cap", stopped("cap"), "stop-circle", "needs"],
  ["conflict", stopped("conflict"), "warning", "needs"],
  ["failed", item({ display_status: "failed", stop: { kind: "failed", node: "implementation", reason: "x", resume_at: null } }), "error", "needs"],
  ["paused", item({ display_status: "paused" }), "debug-pause", "needs"],
  ["not started", item({ display_status: "paused", current_node_id: null }), "debug-pause", "not_started"],
  ["running", item(), "play-circle", "running"],
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

describe("withArchived", () => {
  it.each([
    ["ends a board's groups with Archived", [{ id: "running" }], [{ id: "running" }, { kind: "archived" }]],
    ["adds nothing to an empty board, so its welcome shows", [], []],
  ])("%s", (_, groups, want) => expect(withArchived<object>(groups)).toEqual(want));
});

describe("archivedChildren", () => {
  const held = [item({ id: "restored" }), item({ id: "still", archived_at: "x" })];
  const store = { item: (id: string) => held.find((i) => i.id === id) };
  it.each([
    ["keeps a row the store does not hold", "elsewhere", true],
    ["drops a row the store holds un-archived: it was restored", "restored", false],
    ["keeps a row the store holds archived", "still", true],
  ])("%s", (_, id, kept) =>
    expect(archivedChildren([item({ id, archived_at: "x" })], store).map((i) => i.id)).toEqual(kept ? [id] : []));
  it("draws a row the store holds archived from the store", () =>
    expect(archivedChildren([item({ id: "still", archived_at: "x", title: "stale" })], store)).toEqual([held[1]]));
});

describe("contextValueOf", () => {
  it.each([
    ["the server's actions, in its order", item({ actions: ["pause", "cancel"] }), "item:pause,cancel"],
    ["gate first while one is pending", stopped("gate", { pending_gate: "spec", actions: ["reject", "retry"] }), "item:gate,reject,retry,primaryOpenArtifact"],
    ["no actions from an older server", item(), "item:"],
    ["worktree last while it is on disk", item({ actions: ["pause", "cancel"], worktree_exists: true }), "item:pause,cancel,worktree"],
    ["no worktree once it was reclaimed", item({ actions: ["pause", "cancel"], worktree_exists: false }), "item:pause,cancel"],
    ["a gate with its worktree", stopped("gate", { pending_gate: "spec", actions: ["reject"], worktree_exists: true }), "item:gate,reject,primaryOpenArtifact,worktree"],
    [
      "links after the actions",
      stopped("gate", { pending_gate: "spec", actions: ["approve"], mr_ref: { number: 7, url: "https://g/7" }, attachments: [{ kind: "spec", path: "s.md" }] }),
      "item:gate,approve,mr,attSpec,primaryOpenArtifact",
    ],
    ["the primary token after the drafts and links, before the worktree", stopped("question", { actions: ["resume"], attachments: [{ kind: "plan", path: "p.md" }], worktree_exists: true }), "item:resume,attPlan,primaryAnswer,worktree"],
    ["links before the worktree", item({ actions: ["pause"], attachments: [{ kind: "plan", path: "p.md" }], worktree_exists: true }), "item:pause,attPlan,worktree"],
  ])("%s", (_, it_, want) => {
    expect(contextValueOf(it_)).toBe(want);
  });

  it.each([
    ["drafts after the actions, before the worktree", { drafts: true }, "item:reject,drafts,worktree"],
    ["no drafts token without drafts", { drafts: false }, "item:reject,worktree"],
  ])("%s", (_, opts, want) => expect(contextValueOf(item({ actions: ["reject"], worktree_exists: true }), opts)).toBe(want));

  it("drafts before the links", () =>
    expect(contextValueOf(item({ actions: ["reject"], mr_ref: { number: 7, url: "https://g/7" } }), { drafts: true })).toBe("item:reject,drafts,mr"));

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
  ["a question", stopped("question", { stop: { kind: "question", node: "implementation", reason: "needs_context: Keep the header?", resume_at: null } }), "agent asks: Keep the header?"],
  ["a stop with no reason", stopped("cap"), "waiting for you at implementation"],
  ["a gate, in the board's words", stopped("gate", { pending_gate: "spec_approval" }), "approve spec"],
  ["a row with no display status", item({ display_status: undefined }), "active"],
])("describes %s", (_, it_, want) => {
  expect(describeItem(it_)).toBe(want);
});

it.each([
  ["a gate", stopped("gate", { pending_gate: "spec_approval" }), "list.warningForeground"],
  ["a question", stopped("question"), "list.warningForeground"],
  ["a failure", item({ display_status: "failed" }), "list.errorForeground"],
  ["a cap", stopped("cap"), "list.errorForeground"],
  ["a running row", item(), undefined],
])("colour: %s", (_, it_, want) => expect(colorOf(it_)).toBe(want));

it.each([
  ["a gate", stopped("gate", { title: "Cache", pending_gate: "spec_approval" }), "Cache, needs you, approve spec"],
  ["a running row", item({ title: "Cache" }), "Cache, running, implementation"],
])("screen reader label: %s", (_, it_, want) => expect(itemLabel(it_)).toBe(want));

it.each([[3, "Needs you, 3 items"], [1, "Needs you, 1 item"]])("group label for %s", (n, want) => expect(groupLabel("Needs you", n)).toBe(want));

it.each([
  ["a question, without its prefix", stopped("question", { stop: { kind: "question", node: "implementation", reason: "needs_context: Keep the header?", resume_at: null } }), "K-1 — Keep the header?"],
  ["a cap's whole reason", stopped("cap", { stop: { kind: "cap", node: "implementation", reason: "Loop cap hit. Raise it to retry.", resume_at: null } }), "K-1 — Loop cap hit. Raise it to retry."],
  ["a stop with no reason", stopped("cap"), "K-1 — waiting for you at implementation"],
  ["a running row", item(), "K-1 — implementation"],
  ["a row with no tail", item({ display_status: "someday" as never }), "K-1 — someday"],
  ["a row with no display status", item({ display_status: undefined }), "K-1 — active"],
])("tooltip: %s", (_, it_, want) => {
  expect(tooltipOf(it_)).toBe(want);
});

it.each([
  ["no detail", item(), undefined, "K-1 — implementation"],
  ["a capped spend, no running time", item(), { budget_cap: { cap_usd: 5, spent_usd: 1, source: "item" as const }, running_time: null }, "K-1 — implementation\nSpent $1.00 of $5.00"],
  ["spend and time", stopped("cap", { stop: { kind: "cap", node: "implementation", reason: "Loop cap hit.", resume_at: null } }), { budget_cap: { cap_usd: null, spent_usd: 0.12, source: "policy" as const }, running_time: { running_s: 240, cap_minutes: null } }, "K-1 — Loop cap hit.\nSpent $0.120 (no cap)\nRan 4m (no cap)"],
  ["a detail with neither", item(), {}, "K-1 — implementation"],
])("tooltip with detail: %s", (_, it_, detail, want) => {
  expect(tooltipOf(it_, detail)).toBe(want);
});

it("tooltip: a row alone has no spend line", () => expect(tooltipOf(item())).not.toContain("Spent"));

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
  ["this repo is not connected", true, true, false, 0, true, "connect"],
  ["connected, nothing in it", true, true, false, 0, false, "here"],
  ["unconnected, but something visible", true, true, false, 3, true, "none"],
  ["unconnected, showing every repo", true, true, true, 0, true, "none"],
  ["unconnected, scope not ready", true, false, false, 0, true, "none"],
])("welcome with unconnected: %s", (_, connected, ready, showAll, visible, unconnected, want) => {
  expect(welcomeState(connected, ready, showAll, visible, unconnected)).toBe(want);
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

it.each([
  ["running", item(), false],
  ["paused mid-chain", item({ status: "paused", display_status: "paused" }), true],
  ["stopped for a person", stopped("gate"), true],
  ["done", item({ status: "completed", display_status: "done" }), true],
])("hover from a cached detail: %s", (_, it_, want) => expect(hoverFromCache(it_)).toBe(want));

describe("Board, an item's row", () => {
  const row = item();
  const capped = { ...row, budget_cap: { cap_usd: 5, spent_usd: 1, source: "item" as const }, running_time: null };
  const boardOn = (detail: () => unknown, loadDetail: () => Promise<unknown>, current = row) => {
    const changed = vi.fn();
    const store = { items: () => [current], item: () => current, detail, loadDetail,
      refresh: async () => (changed(), true),
      onChange: () => () => {}, onConnection: () => () => {}, onError: () => () => {}, onEvent: () => () => {}, connected: true } as unknown as Store;
    const board = new Board(store, () => true, { onChange: () => () => {}, ready: true, showAll: false });
    board.onDidChangeTreeData(changed);
    return { board, changed };
  };
  const node = { kind: "item", item: row } as never;

  it("is set at once from a cached detail while the item is not running", () => {
    const paused = item({ status: "paused", display_status: "paused" });
    const pausedNode = { kind: "item", item: paused } as never;
    expect(boardOn(() => capped, vi.fn(), paused).board.getTreeItem(pausedNode).tooltip).toBe("K-1 — paused at implementation\nSpent $1.00 of $5.00");
  });

  it("is re-read while the item runs, though a detail is cached: its running time grows", async () => {
    const loadDetail = vi.fn(async () => capped);
    const { board } = boardOn(() => capped, loadDetail);
    const t = board.getTreeItem(node);
    expect(t.tooltip).toBeUndefined();
    await board.resolveTreeItem(t, node);
    expect(loadDetail).toHaveBeenCalledWith("K-1");
  });

  it("is left unset without a detail, then resolved from a quiet read that redraws nothing", async () => {
    const loadDetail = vi.fn(async () => capped);
    const { board, changed } = boardOn(() => undefined, loadDetail);
    const t = board.getTreeItem(node);
    expect(t.tooltip).toBeUndefined();
    expect((await board.resolveTreeItem(t, node)).tooltip).toBe("K-1 — implementation\nSpent $1.00 of $5.00");
    expect(loadDetail).toHaveBeenCalledWith("K-1");
    expect(changed).not.toHaveBeenCalled();
  });

  it("takes its first line from the store's current row", async () => {
    const { board } = boardOn(() => undefined, async () => capped, stopped("cap", { stop: { kind: "cap", node: "implementation", reason: "Loop cap hit.", resume_at: null } }));
    const t = board.getTreeItem(node);
    expect((await board.resolveTreeItem(t, node)).tooltip).toBe("K-1 — Loop cap hit.\nSpent $1.00 of $5.00");
  });

  it.each([
    ["a gate opens its document", stopped("gate", { pending_gate: "spec_approval" }), "kraft.openArtifact"],
    ["anything else only selects", item(), undefined],
  ])("on click, %s", (_, current, want) => {
    const at = { kind: "item", item: current } as never;
    const command = want && { command: want, title: "Open", arguments: [{ item: current }] };
    expect(boardOn(() => undefined, vi.fn(), current).board.getTreeItem(at).command).toEqual(command);
  });

  it.each([
    ["a gate", stopped("gate", { title: "Cache", pending_gate: "spec_approval" }), "pass", "list.warningForeground", "Cache, needs you, approve spec"],
    ["a running row", item({ title: "Cache" }), "play-circle", undefined, "Cache, running, implementation"],
  ])("draws %s with its icon colour and a screen reader label", (_, current, icon, color, label) => {
    const t = boardOn(() => undefined, vi.fn(), current).board.getTreeItem({ kind: "item", item: current } as never);
    expect([(t.iconPath as { id: string; color?: { id: string } }).id, (t.iconPath as { color?: { id: string } }).color?.id, t.accessibilityInformation?.label]).toEqual([icon, color, label]);
  });

  it("falls back to the row's own line when the read fails", async () => {
    const { board } = boardOn(() => undefined, vi.fn().mockRejectedValue(new Error("down")));
    const t = board.getTreeItem(node);
    expect((await board.resolveTreeItem(t, node)).tooltip).toBe("K-1 — implementation");
  });
});

describe("Board, revealing an item", () => {
  const items = [item({ id: "run" }), item({ id: "other", repo: "/elsewhere" })];
  const store = { items: () => items, item: (id: string) => items.find((i) => i.id === id), detail: () => undefined,
    onChange: () => () => {}, onConnection: () => () => {}, onError: () => () => {}, onEvent: () => () => {}, connected: true } as unknown as Store;
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
    vi.spyOn(vscode.window, "createTreeView").mockReturnValue({ reveal, onDidExpandElement: () => ({ dispose() {} }), dispose() {} } as never);
    const { reveal: show } = registerBoard({ subscriptions: [] } as never, store, {} as never, () => null, here, scope);
    await expect(show("run")).resolves.toBe(true);
    await show("other");
    expect(reveal.mock.calls).toEqual([[{ kind: "item", item: items[0] }, { select: true, focus: false }]]);
  });

  it.each([
    ["an item it shows, even before the view resolves", "run", true],
    ["an item outside the scope", "other", false],
    ["an archived item", "arch", false],
    ["an id it does not know", "nope", false],
  ])("says whether it holds %s", async (_, id, want) => {
    const held = [...items, item({ id: "arch", display_status: "archived", archived_at: "x" })];
    const s = { ...store, items: () => held, item: (i: string) => held.find((x) => x.id === i) } as unknown as Store;
    vi.spyOn(vscode.window, "createTreeView").mockReturnValue({ reveal: vi.fn().mockRejectedValue(new Error("not resolved yet")), onDidExpandElement: () => ({ dispose() {} }), dispose() {} } as never);
    const { reveal } = registerBoard({ subscriptions: [] } as never, s, {} as never, () => null, here, scope);
    expect(await reveal(id)).toBe(want);
  });
});

describe("Answer", () => {
  it.each([
    ["resumes the clicked row with the answer as its steer", () => null, [["Q-1", "use v2"]]],
    ["does nothing while read-only", () => "Kraft is read-only.", []],
  ])("%s", async (_, refusal, resumed) => {
    const asked = stopped("question", { id: "Q-1", actions: ["resume", "retry"], stop: { kind: "question", node: "implementation", reason: "Which API?", resume_at: null } });
    const store = { items: () => [asked], item: () => asked, detail: () => undefined, refresh: async () => true,
      onChange: () => () => {}, onConnection: () => () => {}, onError: () => () => {}, onEvent: () => () => {}, connected: true } as unknown as Store;
    const api = { resume: vi.fn(async () => {}), retry: vi.fn(async () => {}) };
    const handlers = new Map<string, (...a: unknown[]) => unknown>();
    vi.spyOn(vscode.commands, "registerCommand").mockImplementation(((id: string, cb: (...a: unknown[]) => unknown) => (handlers.set(id, cb), { dispose() {} })) as never);
    const input = vi.spyOn(vscode.window, "showInputBox").mockResolvedValue("use v2");
    registerBoard({ subscriptions: [] } as never, store, api as never, refusal, () => true, { onChange: () => () => {}, ready: true, showAll: false });
    // A selection passed along is not the Answer's: it answers the clicked row.
    const other = stopped("question", { id: "Q-2", actions: ["resume", "retry"] });
    await handlers.get("kraft.answer")!({ kind: "item", item: asked }, [{ kind: "item", item: asked }, { kind: "item", item: other }]);
    expect(api.resume.mock.calls).toEqual(resumed);
    expect(api.retry).not.toHaveBeenCalled();
    if (resumed.length) expect(input).toHaveBeenCalledWith({ prompt: "Q-1 asks: Which API? Your answer steers it (optional)" });
  });
  afterEach(() => vi.restoreAllMocks());
});

describe("A multi-selection", () => {
  const row = (i: ReturnType<typeof item>) => ({ kind: "item", item: i });
  const [p1, p2] = [item({ id: "P-1", actions: ["pause"] }), item({ id: "P-2", actions: ["pause"] })];
  const [r1, r2] = [item({ id: "R-1", display_status: "paused", actions: ["resume"] }), item({ id: "R-2", display_status: "paused", actions: ["resume"] })];
  const q = stopped("question", { id: "Q-1", actions: ["resume", "retry"] });
  const [d1, d2] = [item({ id: "D-1", display_status: "done", actions: ["archive"] }), item({ id: "D-2", display_status: "done", actions: ["archive"] })];
  const p3 = item({ id: "P-3", actions: ["pause"] });
  /** The ids each bulk call sent, without the results array it fills. */
  const sent = (bulk: { mock: { calls: unknown[][] } }) => bulk.mock.calls.map((c) => c.slice(0, 2));

  function setup(refusal: () => string | null = () => null, answer: string | null = "Archive") {
    const all = [p1, p2, p3, r1, r2, q, d1, d2];
    const refreshed: string[] = [];
    const store = { items: () => all, item: (id: string) => all.find((i) => i.id === id), detail: () => undefined,
      refresh: async (id: string) => { refreshed.push(id); if (id === "P-2") throw new Error("gone"); return true; },
      onChange: () => () => {}, onConnection: () => () => {}, onError: () => () => {}, onEvent: () => () => {}, connected: true } as unknown as Store;
    const api = {
      pause: vi.fn(async () => {}), archive: vi.fn(async () => {}), retry: vi.fn(async () => {}),
      resume: vi.fn(async (id: string) => { if (id === "R-2") throw new ApiError(409, "boom"); }),
      // P-3's batch times out after P-1's went through.
      bulk: vi.fn(async (_a: string, ids: string[], out: BulkResult[] = []) => {
        for (const id of ids) {
          if (id === "P-3") throw new ApiError(0, "Kraft did not answer within 60s");
          out.push(id === "P-2" ? { id, ok: false, error: "being started" } : { id, ok: true });
        }
        return out;
      }),
    };
    const handlers = new Map<string, (...a: unknown[]) => unknown>();
    const tree = vi.spyOn(vscode.window, "createTreeView");
    vi.spyOn(vscode.commands, "registerCommand").mockImplementation(((id: string, cb: (...a: unknown[]) => unknown) => (handlers.set(id, cb), { dispose() {} })) as never);
    const info = vi.spyOn(vscode.window, "showInformationMessage").mockResolvedValue(undefined);
    const warn = vi.spyOn(vscode.window, "showWarningMessage").mockImplementation((async (_m: string, o?: unknown) => (o ? answer ?? undefined : undefined)) as never);
    const input = vi.spyOn(vscode.window, "showInputBox").mockResolvedValue("");
    const logged = vi.spyOn(log, "info");
    registerBoard({ subscriptions: [] } as never, store, api as never, refusal, () => true, { onChange: () => () => {}, ready: true, showAll: false });
    return { handlers, api, info, warn, input, refreshed, tree, logged };
  }

  afterEach(() => vi.restoreAllMocks());

  it("lets the board select many rows", () => {
    const { tree } = setup();
    expect(tree.mock.calls[0][1]).toMatchObject({ canSelectMany: true });
  });

  it("pauses in one bulk call, warns with the refusal, logs it, and refreshes each, a failed refresh ignored", async () => {
    const { handlers, api, warn, info, refreshed, logged } = setup();
    await handlers.get("kraft.pause")!(row(p1), [row(p1), row(p2)]);
    expect(sent(api.bulk)).toEqual([["pause", ["P-1", "P-2"]]]);
    expect(logged).toHaveBeenCalledWith("Paused 1. Not paused: P-2 (being started).");
    expect(api.pause).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledWith("Paused 1. Not paused: P-2 (being started).");
    expect(info).not.toHaveBeenCalled();
    expect(refreshed).toEqual(["P-1", "P-2"]);
  });

  it("resumes one id at a time, in order and with no steer, and leaves a question to its answer", async () => {
    const { handlers, api, warn, input } = setup();
    await handlers.get("kraft.resume")!(row(r1), [row(r1), row(q), row(r2)]);
    expect(api.resume.mock.calls).toEqual([["R-1"], ["R-2"]]);
    expect(input).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledWith("Resumed 1. Not resumed: R-2 (boom), Q-1 (needs an answer).");
  });

  it("still refreshes each id when a batch fails, and says what went through", async () => {
    const { handlers, warn, refreshed } = setup();
    await handlers.get("kraft.pause")!(row(p1), [row(p1), row(p3)]);
    expect(refreshed).toEqual(["P-1", "P-3"]);
    expect(warn).toHaveBeenCalledWith("Paused 1. Not paused: P-3 (Kraft did not answer within 60s).");
  });

  it.each([
    ["archives after asking once", "Archive", [d1, d2], [["archive", ["D-1", "D-2"]]], "Archive 2 items? Their worktrees are deleted.", "Archived 2."],
    ["archives nothing when the question is dismissed", null, [d1, d2], [], "Archive 2 items? Their worktrees are deleted.", undefined],
    ["asks nothing when only one of the selection can be archived", "Archive", [d1, p1], [["archive", ["D-1"]]], undefined, undefined],
  ])("%s", async (_, answer, selection, calls, asked, said) => {
    const { handlers, api, warn, info } = setup(() => null, answer);
    await handlers.get("kraft.archive")!(row(selection[0]), selection.map(row));
    expect(warn.mock.calls.filter((c) => c.length > 1).map((c) => c[0])).toEqual(asked ? [asked] : []);
    expect(sent(api.bulk)).toEqual(calls);
    expect(info.mock.calls).toEqual(said ? [[said]] : []);
  });

  it.each([
    ["one selected row", [row(p1)]],
    ["a click outside the selection", [row(p2)]],
  ])("keeps the single-item path for %s", async (_, nodes) => {
    const { handlers, api } = setup();
    await handlers.get("kraft.pause")!(row(p1), nodes);
    expect(api.pause.mock.calls).toEqual([["P-1"]]);
    expect(api.bulk).not.toHaveBeenCalled();
  });

  it("does nothing while read-only, and warns once", async () => {
    const { handlers, api, warn } = setup(() => "Kraft is read-only.");
    await handlers.get("kraft.pause")!(row(p1), [row(p1), row(p2)]);
    expect(api.bulk).not.toHaveBeenCalled();
    expect(warn.mock.calls).toEqual([["Kraft is read-only."]]);
  });

  it("leaves the selection to those four: retry acts on the clicked row", async () => {
    const failed = item({ id: "F-1", display_status: "failed", actions: ["retry"] });
    const { handlers, api } = setup();
    await handlers.get("kraft.retry")!(row(failed), [row(failed), row(p1)]);
    expect(api.retry.mock.calls).toEqual([["F-1", undefined]]);
  });
});

describe("The Archived group", () => {
  const live = [item({ id: "run" })];
  const old = [item({ id: "A-1", archived_at: "x", actions: ["restore"] }), item({ id: "A-2", repo: "/elsewhere", archived_at: "x" })];
  const here = (i: { repo: string }) => i.repo === "/r";
  function setup(list: () => Promise<ReturnType<typeof item>[]> = async () => old, items = live) {
    const conn = new Set<(up: boolean) => void>();
    const evs = new Set<(ev: { type: string }) => void>();
    const store = { items: () => items, item: (id: string) => items.find((i) => i.id === id), detail: () => undefined,
      onChange: () => () => {}, onError: () => () => {}, connected: true,
      onConnection: (cb: (up: boolean) => void) => (conn.add(cb), () => {}),
      onEvent: (cb: (ev: { type: string }) => void) => (evs.add(cb), () => {}) } as unknown as Store;
    const listArchived = vi.fn(list);
    const board = new Board(store, here, { onChange: () => () => {}, ready: true, showAll: false }, undefined, undefined, listArchived);
    const changed = vi.fn();
    board.onDidChangeTreeData(changed);
    const archived = () => board.getChildren().at(-1)!;
    const rows = () => board.getChildren(archived() as never).map((n) => (n as { item?: { id: string }; text?: string }).item?.id ?? (n as { text: string }).text);
    return { board, listArchived, changed, archived, rows, connect: (up: boolean) => conn.forEach((cb) => cb(up)), send: (type: string) => evs.forEach((cb) => cb({ type })) };
  }
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("is a collapsed row last on the board, keyed group:archived", () => {
    const { board, archived } = setup();
    const t = board.getTreeItem(archived() as never);
    expect([t.label, t.collapsibleState, t.id, t.contextValue]).toEqual(["Archived", vscode.TreeItemCollapsibleState.Collapsed, "group:archived", "archived"]);
  });

  it("draws from its cache and never reads: empty before the first read", () => {
    const { rows, listArchived } = setup();
    expect(rows()).toEqual([]);
    expect(listArchived).not.toHaveBeenCalled();
  });

  it.each([
    ["a connect reads it", (s: ReturnType<typeof setup>) => s.connect(true), 1],
    ["a disconnect does not", (s: ReturnType<typeof setup>) => s.connect(false), 0],
  ])("%s", async (_, go, reads) => {
    const s = setup();
    go(s);
    await vi.waitFor(() => expect(s.listArchived).toHaveBeenCalledTimes(reads));
    if (reads) await vi.waitFor(() => expect(s.rows()).toEqual(["A-1"]));
  });

  it("reads once after a burst of archive and restore events, and not for another event", async () => {
    vi.useFakeTimers();
    const s = setup();
    s.send("work_item_archived");
    s.send("work_item_restored");
    s.send("gate_requested");
    await vi.advanceTimersByTimeAsync(150);
    expect(s.listArchived).toHaveBeenCalledTimes(1);
    s.send("gate_requested");
    await vi.advanceTimersByTimeAsync(150);
    expect(s.listArchived).toHaveBeenCalledTimes(1);
  });

  it("leaves out rows outside the scope", async () => {
    const s = setup();
    await s.board.loadArchived();
    expect(s.rows()).toEqual(["A-1"]);
  });

  it.each([
    ["an Error by its message", new Error("socket hang up"), "socket hang up"],
    ["anything else as a string", "odd", "odd"],
  ])("shows a failed read of %s", async (_, thrown, detail) => {
    const s = setup(async () => { throw thrown; });
    await s.board.loadArchived();
    expect(s.rows()).toEqual([`Couldn't load archived items: ${detail}`]);
  });

  it("clears its pending timers when disposed", async () => {
    vi.useFakeTimers();
    const s = setup();
    s.send("work_item_archived");
    s.board.dispose();
    await vi.advanceTimersByTimeAsync(150);
    expect(s.listArchived).not.toHaveBeenCalled();
  });

  it("shows a failed read as one row", async () => {
    const s = setup(async () => { throw new ApiError(500, "db locked"); });
    await s.board.loadArchived();
    expect(s.rows()).toEqual(["Couldn't load archived items: db locked"]);
  });

  it("keeps the newest read when an older one answers last", async () => {
    let first!: (v: ReturnType<typeof item>[]) => void;
    const replies = [new Promise<ReturnType<typeof item>[]>((r) => (first = r)), Promise.resolve([old[0]])];
    const s = setup(() => replies.shift()!);
    const slow = s.board.loadArchived();
    await s.board.loadArchived();
    first([item({ id: "stale", archived_at: "x" })]);
    await slow;
    expect(s.rows()).toEqual(["A-1"]);
  });

  it("is each archived row's parent, and an error row's", () => {
    const s = setup();
    expect(s.board.getParent({ kind: "item", item: old[0] } as never)).toEqual({ kind: "archived" });
    expect(s.board.getParent({ kind: "note", text: "x" } as never)).toEqual({ kind: "archived" });
  });

  it("offers Restore on its rows through the server's actions", async () => {
    const s = setup();
    await s.board.loadArchived();
    const [row] = s.board.getChildren(s.archived() as never);
    expect(s.board.getTreeItem(row as never).contextValue).toBe("item:restore");
  });

  it("is read on its expand, not on a group's", () => {
    let expand!: (e: { element: unknown }) => void;
    vi.spyOn(vscode.window, "createTreeView").mockReturnValue({ reveal: vi.fn(), onDidExpandElement: (cb: typeof expand) => ((expand = cb), { dispose() {} }), dispose() {} } as never);
    const listArchived = vi.fn(async () => ({ items: old, cursor: 0 }));
    const store = { items: () => live, item: () => undefined, detail: () => undefined, connected: true,
      onChange: () => () => {}, onConnection: () => () => {}, onError: () => () => {}, onEvent: () => () => {} } as unknown as Store;
    const subscriptions: unknown[] = [];
    const { board } = registerBoard({ subscriptions } as never, store, { listArchived } as never, () => null, here, { onChange: () => () => {}, ready: true, showAll: false });
    // Disposed with the extension, so its timers do not outlive it.
    expect(subscriptions).toContain(board);
    expand({ element: { kind: "group", id: "running", label: "Running", items: live } });
    expect(listArchived).not.toHaveBeenCalled();
    expand({ element: { kind: "archived" } });
    expect(listArchived).toHaveBeenCalledTimes(1);
  });
});

describe("nextTick", () => {
  const now = Date.parse("2026-10-12T12:00:00Z");
  const waiting = (ms: number, over: Parameters<typeof item>[0] = {}) =>
    item({ display_status: "waiting", retry_at: new Date(now + ms).toISOString(), ...over });
  it.each([
    ["no countdown row", [item(), stopped("question")], undefined],
    ["a waiting row with no clock", [item({ display_status: "waiting" })], undefined],
    ["an expired one, which never changes again", [waiting(-1)], undefined],
    ["a countdown on a row that is not waiting", [item({ retry_at: new Date(now + 5 * 60_000).toISOString() })], undefined],
    ["the minute turning over, 1ms past it", [waiting(5 * 60_000 + 30_000)], 30_001],
    ["exactly on a minute, the next one", [waiting(5 * 60_000)], 1],
    ["in 1m, held until it expires", [waiting(90_000)], 90_000],
    ["under a minute, until it expires", [waiting(20_000)], 20_000],
    ["the hour turning over", [waiting(2 * 3_600_000 + 5 * 60_000)], 5 * 60_000 + 1],
    ["the day turning over", [waiting(2 * 86_400_000 + 3_600_000)], 3_600_001],
    ["the soonest of several", [waiting(10 * 60_000 + 40_000), waiting(3 * 60_000 + 10_000)], 10_001],
    ["a wait stop's resume_at over retry_at", [waiting(9 * 60_000, { stop: { kind: "wait", node: "ci", reason: null, resume_at: new Date(now + 4 * 60_000 + 5_000).toISOString() } })], 5_001],
  ])("%s", (_, items, want) => expect(nextTick(items, now)).toBe(want));
});

describe("Board, a countdown row", () => {
  afterEach(() => vi.useRealTimers());
  const boardOf = (items: () => ReturnType<typeof item>[]) => {
    const store = { items, item: () => undefined, detail: () => undefined,
      onChange: () => () => {}, onConnection: () => () => {}, onError: () => () => {}, onEvent: () => () => {}, connected: true } as unknown as Store;
    const board = new Board(store, () => true, { onChange: () => () => {}, ready: true, showAll: false });
    const fired = vi.fn();
    board.onDidChangeTreeData(fired);
    return { board, fired };
  };
  const at = (ms: number) => item({ display_status: "waiting", retry_at: new Date(Date.now() + ms).toISOString() });

  it("redraws the tree when its text turns over, and keeps counting", () => {
    vi.useFakeTimers();
    const { board, fired } = boardOf(() => [at(5 * 60_000 + 30_000)]);
    board.getChildren();
    vi.advanceTimersByTime(30_000);
    expect(fired).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2);
    expect(fired).toHaveBeenCalledTimes(1);
    board.getChildren();
    vi.advanceTimersByTime(60_000);
    expect(fired).toHaveBeenCalledTimes(2);
  });

  it("stops when no shown row counts down, and when the board is disposed", () => {
    vi.useFakeTimers();
    let rows = [at(5 * 60_000)];
    const { board, fired } = boardOf(() => rows);
    board.getChildren();
    rows = [item()];
    board.getChildren();
    vi.advanceTimersByTime(10 * 60_000);
    expect(fired).not.toHaveBeenCalled();
    rows = [at(5 * 60_000)];
    board.getChildren();
    board.dispose();
    vi.advanceTimersByTime(10 * 60_000);
    expect(fired).not.toHaveBeenCalled();
  });
});
