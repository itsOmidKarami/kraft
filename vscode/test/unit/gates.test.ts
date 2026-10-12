import { afterEach, describe, expect, it, vi } from "vitest";
import * as vscode from "vscode";
import type { ReviewThread } from "../../src/core/api";
import type { Store } from "../../src/core/store";
import type { ThreadCache } from "../../src/core/threadCache";
import { registerGates } from "../../src/gates";
import { stopped } from "./fixtures/item";
import { C, T } from "./fixtures/thread";

const GATE = "spec_approval";
const atGate = stopped("gate", { id: "G-1", pending_gate: GATE, actions: ["approve", "reject"] });
const draft = T({ id: "t-d", comments: [C({ id: "c-d", review_id: null, draft: true })], draft: true });
const mustFix = T({ id: "t-m", label: "must_fix", state: "open" });

/** registerGates over fakes: the gate's document already read (digest `d1`)
 *  unless `unread`, its threads `threads`, and every command it registers in `handlers`. */
function setup({ threads = [] as ReviewThread[], refusal = () => null as string | null, refresh = async () => true, unread = false, target = undefined as unknown } = {}) {
  const handlers = new Map<string, (...a: unknown[]) => unknown>();
  vi.spyOn(vscode.commands, "registerCommand").mockImplementation(((id: string, cb: (...a: unknown[]) => unknown) => (handlers.set(id, cb), { dispose() {} })) as never);
  const changes: ((ids: string[] | "all") => void)[] = [];
  const store = {
    items: () => [atGate], item: (id: string) => (id === atGate.id ? atGate : undefined),
    refresh: vi.fn(refresh), onChange: (cb: (ids: string[] | "all") => void) => (changes.push(cb), () => {}),
  } as unknown as Store;
  const api = {
    approve: vi.fn(async () => {}),
    submitReview: vi.fn(async () => ({})),
    reject: vi.fn(async () => {}),
    getArtifact: vi.fn(async () => ({ title: "spec", content: "# spec", digest: "d1", truncated: false })),
  };
  const cache = { load: vi.fn(async () => threads) } as unknown as ThreadCache;
  const preview = { target: () => target, opening: () => {} };
  const scope = { onChange: () => () => {}, showAll: false };
  const gates = registerGates({ subscriptions: [] } as never, store, api as never, refusal, () => true, scope, cache, async () => true, preview as never);
  if (!unread) gates.shown.record(atGate.id, GATE, "d1");
  return { handlers, api, store, changes };
}

const spies = () => ({
  ask: vi.spyOn(vscode.window, "showWarningMessage"),
  error: vi.spyOn(vscode.window, "showErrorMessage"),
  pick: vi.spyOn(vscode.window, "showQuickPick"),
});

afterEach(() => vi.restoreAllMocks());

describe("kraft.approve", () => {
  it.each<[string, Parameters<typeof setup>[0], string | undefined, { approve: unknown[][]; submitReview: unknown[][]; refused: boolean }]>([
    ["no drafts: the gate's approve, with the digest read", {}, undefined,
      { approve: [["G-1", GATE, "d1"]], submitReview: [], refused: false }],
    ["drafts, confirmed: one review carries them with the approval", { threads: [draft] }, "Approve and Send",
      { approve: [], submitReview: [["G-1", GATE, { outcome: "approve", digest: "d1" }]], refused: false }],
    ["drafts, declined: nothing is sent", { threads: [draft] }, undefined,
      { approve: [], submitReview: [], refused: false }],
    ["a must-fix thread open: refused before anything is sent", { threads: [mustFix] }, "Approve and Send",
      { approve: [], submitReview: [], refused: true }],
  ])("%s", async (_, opts, answer, want) => {
    const { handlers, api } = setup(opts);
    const { ask, pick } = spies();
    ask.mockResolvedValue(answer as never);
    await handlers.get("kraft.approve")!("G-1", GATE);
    expect({ approve: api.approve.mock.calls, submitReview: api.submitReview.mock.calls, refused: pick.mock.calls.length > 0 }).toEqual(want);
  });

  it("from an older render's preview, opens the latest instead of approving", async () => {
    const webview = { scheme: "webview-panel", path: "/x" };
    const { handlers, api } = setup({ target: { id: "G-1", basename: "an older render (G-1).md" } });
    await handlers.get("kraft.approve")!(webview);
    expect([api.approve.mock.calls, api.getArtifact.mock.calls]).toEqual([[], [["G-1"]]]);
  });
});

describe("a failed re-read after the action went through", () => {
  it.each([
    ["approve", (h: Map<string, (...a: unknown[]) => unknown>) => h.get("kraft.approve")!("G-1", GATE), "approve"],
    ["reject", (h: Map<string, (...a: unknown[]) => unknown>) => h.get("kraft.reject")!("G-1", GATE, "no"), "reject"],
  ] as const)("%s: is not reported as the action failing", async (_, run, called) => {
    const { handlers, api, store } = setup({ refresh: async () => { throw new Error("socket hang up"); } });
    const { error } = spies();
    await run(handlers);
    expect([api[called].mock.calls.length, vi.mocked(store.refresh).mock.calls.length, error.mock.calls]).toEqual([1, 1, []]);
  });
});

describe("a gate's notification", () => {
  it.each([
    ["offers Open and Reject…", null, ["Open", "Reject…"]],
    ["read-only: offers Open only", "Kraft is read-only.", ["Open"]],
  ])("%s", (_, why, buttons) => {
    const { changes } = setup({ refusal: () => why });
    const toast = vi.spyOn(vscode.window, "showInformationMessage");
    changes.forEach((cb) => cb([atGate.id]));
    expect(toast.mock.calls.map((c) => c.slice(1))).toEqual([buttons]);
  });
});
