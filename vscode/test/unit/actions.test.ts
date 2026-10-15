import { afterEach, expect, it, vi } from "vitest";
import * as vscode from "vscode";
import { runAction } from "../../src/actions";
import { restartInTerminal } from "../../src/config/check";
import { RESTART_FAILED, RESTART_SKIPPED } from "../../src/core/start";
import type { Store } from "../../src/core/store";
import { offerWarnings } from "../../src/fileWork";
import { item } from "./fixtures/item";

afterEach(() => vi.restoreAllMocks());

const failed = async () => { throw new Error("socket hang up"); };

// A second Skip would skip the next node: an action that went through is not reported as failed.
it.each<[string, () => Promise<boolean>, (api: never, store: Store) => Promise<void>, string]>([
  ["a Skip, then its re-read", async () => true, (api, store) => runAction(api, store, item({ id: "S-1" }), "skip"), "Skip"],
  ["a Skip, then its re-read failed", failed, (api, store) => runAction(api, store, item({ id: "S-1" }), "skip"), "Skip"],
  ["an Abandon from the filing warning, then its re-read failed", failed,
    (api, store) => offerWarnings({ api, store } as never, "S-1", ["Same title as S-0."], ["Abandon"]), "Abandon"],
])("%s: reports no failure", async (_, refresh, run, answer) => {
  const api = { skip: vi.fn(async () => {}), abandon: vi.fn(async () => {}) };
  const store = { refresh: vi.fn(refresh), item: () => item({ id: "S-1", display_status: "paused", current_node_id: null, actions: ["abandon"] }) } as unknown as Store;
  vi.spyOn(vscode.window, "showWarningMessage").mockResolvedValue(answer as never);
  const error = vi.spyOn(vscode.window, "showErrorMessage");
  await run(api as never, store);
  expect([[...api.skip.mock.calls, ...api.abandon.mock.calls], vi.mocked(store.refresh).mock.calls, error.mock.calls]).toEqual([[["S-1"]], [["S-1"]], []]);
});

// An item is named by its title, not its id.
it.each<[string, "skip" | "cancel" | "escalate", "showWarningMessage" | "showInputBox", string]>([
  ["a Skip's question", "skip", "showWarningMessage", "Skip Cache layer's current step?"],
  ["a Cancel's reason prompt", "cancel", "showInputBox", "Why cancel Cache layer? (recorded on the item)"],
  ["an escalation's prompt", "escalate", "showInputBox", "What should Kraft-Agent help with on Cache layer?"],
])("%s names the item by its title", async (_, action, ask, want) => {
  const store = { refresh: vi.fn(async () => true), item: () => undefined } as unknown as Store;
  const asked = vi.spyOn(vscode.window, ask).mockResolvedValue(undefined as never);
  await runAction({} as never, store, item({ id: "S-1", title: "Cache layer" }), action);
  expect(asked.mock.calls.map((c) => (typeof c[0] === "string" ? c[0] : (c[0] as { prompt: string }).prompt))).toEqual([want]);
});

// `kraft admin restart` on a Kraft attached to a terminal stops it and exits 1; the
// terminal running it closes with its message, so the extension says what happened.
// It also exits 1 with Kraft still up (no to its running-agents question, nothing
// running): Kraft still answering is what tells the two apart.
it.each<[string, { code: number | undefined } | undefined, boolean, boolean, string | undefined, unknown[][], unknown[][]]>([
  ["exited 1: says Kraft may be stopped, offers Start", { code: 1 }, true, false, undefined, [[RESTART_FAILED(1), "Start Kraft"]], []],
  ["exited 1, Start picked: starts Kraft", { code: 1 }, true, false, "Start Kraft", [[RESTART_FAILED(1), "Start Kraft"]], [["kraft.startDaemon"]]],
  ["exited 1, Kraft still answering: says it still runs, no Start", { code: 1 }, true, true, undefined, [[RESTART_SKIPPED(1)]], []],
  ["exited 0: nothing to say", { code: 0 }, true, false, undefined, [], []],
  ["closed by hand: nothing to say", { code: undefined }, true, false, undefined, [], []],
  ["another terminal closing", { code: 1 }, false, false, undefined, [], []],
])("the restart terminal %s", async (_, exitStatus, ours, up, pick, said, ran) => {
  const closes: ((t: unknown) => void)[] = [];
  const term = { sendText() {}, show() {}, exitStatus: undefined as unknown };
  vi.spyOn(vscode.window, "createTerminal").mockReturnValue(term as never);
  vi.spyOn(vscode.window, "onDidCloseTerminal").mockImplementation(((cb: (t: unknown) => void) => (closes.push(cb), { dispose() {} })) as never);
  const warn = vi.spyOn(vscode.window, "showWarningMessage").mockResolvedValue(pick as never);
  const exec = vi.spyOn(vscode.commands, "executeCommand");
  const api = { health: vi.fn(async () => { if (!up) throw new Error("Kraft is not reachable"); return {}; }) };
  restartInTerminal(api as never);
  const closed = ours ? Object.assign(term, { exitStatus }) : { exitStatus };
  for (const cb of closes) await cb(closed);
  expect([warn.mock.calls, exec.mock.calls]).toEqual([said, ran]);
});

// Archive deletes the worktree, as the bulk archive says: one confirms too, unless there is no worktree.
it.each<[string, boolean, string | undefined, string[], number]>([
  ["with a worktree, confirmed", true, "Archive", ["Archive Cache embeddings? Its worktree is deleted."], 1],
  ["with a worktree, declined", true, undefined, ["Archive Cache embeddings? Its worktree is deleted."], 0],
  ["without a worktree: no question", false, undefined, [], 1],
])("a single archive %s", async (_, worktree, answer, asked, archived) => {
  const api = { archive: vi.fn(async () => {}) };
  const store = { refresh: vi.fn(async () => true) } as unknown as Store;
  const ask = vi.spyOn(vscode.window, "showWarningMessage").mockResolvedValue(answer as never);
  await runAction(api as never, store, item({ id: "A-1", title: "Cache embeddings", worktree_exists: worktree }), "archive");
  expect([ask.mock.calls.map((c) => c[0]), api.archive.mock.calls.length]).toEqual([asked, archived]);
});
