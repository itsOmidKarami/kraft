import { afterEach, describe, expect, it, vi } from "vitest";
import * as vscode from "vscode";
import { Board, registerBoard } from "../../src/board";
import { ApiError } from "../../src/core/api";
import type { Store } from "../../src/core/store";
import { announceable, contextValueOf, hoverFromCache, describe as describeItem, groupHolding, groupItems, iconOf, needsYouCount, steerPrompt, tooltipOf, welcomeState, worktreeTarget } from "../../src/core/board";
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
  ["no detail", item(), undefined, "K-1 — implementation"],
  ["a capped spend, no running time", item(), { budget_cap: { cap_usd: 5, spent_usd: 1, source: "item" as const }, running_time: null }, "K-1 — implementation\nSpent $1.00 of $5.00"],
  ["spend and time", stopped("cap", { stop: { kind: "cap", node: "implementation", reason: "Loop cap hit.", resume_at: null } }), { budget_cap: { cap_usd: null, spent_usd: 0.12, source: "policy" as const }, running_time: { running_s: 240, cap_minutes: null } }, "Loop cap hit.\nSpent $0.120 (no cap)\nRan 4m (no cap)"],
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
      onChange: () => () => {}, onConnection: () => () => {}, onError: () => () => {}, connected: true } as unknown as Store;
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
    expect((await board.resolveTreeItem(t, node)).tooltip).toBe("Loop cap hit.\nSpent $1.00 of $5.00");
  });

  it.each([
    ["a gate opens its document", stopped("gate", { pending_gate: "spec_approval" }), "kraft.openArtifact"],
    ["anything else opens the web UI", item(), "kraft.openInWebUi"],
  ])("on click, %s, for the clicked row", (_, current, command) => {
    const at = { kind: "item", item: current } as never;
    expect(boardOn(() => undefined, vi.fn(), current).board.getTreeItem(at).command).toEqual({ command, title: "Open", arguments: [{ item: current }] });
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
    vi.spyOn(vscode.window, "createTreeView").mockReturnValue({ reveal: vi.fn().mockRejectedValue(new Error("not resolved yet")), dispose() {} } as never);
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
      onChange: () => () => {}, onConnection: () => () => {}, onError: () => () => {}, connected: true } as unknown as Store;
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
    vi.restoreAllMocks();
  });
});

describe("A multi-selection", () => {
  const row = (i: ReturnType<typeof item>) => ({ kind: "item", item: i });
  const [p1, p2] = [item({ id: "P-1", actions: ["pause"] }), item({ id: "P-2", actions: ["pause"] })];
  const [r1, r2] = [item({ id: "R-1", display_status: "paused", actions: ["resume"] }), item({ id: "R-2", display_status: "paused", actions: ["resume"] })];
  const q = stopped("question", { id: "Q-1", actions: ["resume", "retry"] });
  const [d1, d2] = [item({ id: "D-1", display_status: "done", actions: ["archive"] }), item({ id: "D-2", display_status: "done", actions: ["archive"] })];

  function setup(refusal: () => string | null = () => null, answer: string | null = "Archive") {
    const all = [p1, p2, r1, r2, q, d1, d2];
    const refreshed: string[] = [];
    const store = { items: () => all, item: (id: string) => all.find((i) => i.id === id), detail: () => undefined,
      refresh: async (id: string) => { refreshed.push(id); if (id === "P-2") throw new Error("gone"); return true; },
      onChange: () => () => {}, onConnection: () => () => {}, onError: () => () => {}, connected: true } as unknown as Store;
    const api = {
      pause: vi.fn(async () => {}), archive: vi.fn(async () => {}), retry: vi.fn(async () => {}),
      resume: vi.fn(async (id: string) => { if (id === "R-2") throw new ApiError(409, "boom"); }),
      bulk: vi.fn(async (_a: string, ids: string[]) => ids.map((id) => (id === "P-2" ? { id, ok: false, error: "being started" } : { id, ok: true }))),
    };
    const handlers = new Map<string, (...a: unknown[]) => unknown>();
    const tree = vi.spyOn(vscode.window, "createTreeView");
    vi.spyOn(vscode.commands, "registerCommand").mockImplementation(((id: string, cb: (...a: unknown[]) => unknown) => (handlers.set(id, cb), { dispose() {} })) as never);
    const info = vi.spyOn(vscode.window, "showInformationMessage").mockResolvedValue(undefined);
    const warn = vi.spyOn(vscode.window, "showWarningMessage").mockImplementation((async (_m: string, o?: unknown) => (o ? answer ?? undefined : undefined)) as never);
    const input = vi.spyOn(vscode.window, "showInputBox").mockResolvedValue("");
    registerBoard({ subscriptions: [] } as never, store, api as never, refusal, () => true, { onChange: () => () => {}, ready: true, showAll: false });
    return { handlers, api, info, warn, input, refreshed, tree };
  }

  afterEach(() => vi.restoreAllMocks());

  it("lets the board select many rows", () => {
    const { tree } = setup();
    expect(tree.mock.calls[0][1]).toMatchObject({ canSelectMany: true });
  });

  it("pauses in one bulk call, warns with the refusal, and refreshes each, a failed refresh ignored", async () => {
    const { handlers, api, warn, info, refreshed } = setup();
    await handlers.get("kraft.pause")!(row(p1), [row(p1), row(p2)]);
    expect(api.bulk.mock.calls).toEqual([["pause", ["P-1", "P-2"]]]);
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

  it.each([
    ["archives after asking once", "Archive", [["archive", ["D-1", "D-2"]]], "Archived 2."],
    ["archives nothing when the question is dismissed", null, [], undefined],
  ])("%s", async (_, answer, calls, said) => {
    const { handlers, api, warn, info } = setup(() => null, answer);
    await handlers.get("kraft.archive")!(row(d1), [row(d1), row(d2)]);
    expect(warn.mock.calls[0]).toEqual(["Archive 2 items? Their worktrees are deleted.", { modal: true }, "Archive"]);
    expect(api.bulk.mock.calls).toEqual(calls);
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
