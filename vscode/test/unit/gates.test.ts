import { afterEach, describe, expect, it, vi } from "vitest";
import * as vscode from "vscode";
import type { ReviewThread } from "../../src/core/api";
import type { Store } from "../../src/core/store";
import type { ThreadCache } from "../../src/core/threadCache";
import { registerGates } from "../../src/gates";
import { item, stopped } from "./fixtures/item";
import { C, T } from "./fixtures/thread";

const GATE = "spec_approval";
const atGate = stopped("gate", { id: "G-1", title: "Cache layer", pending_gate: GATE, actions: ["approve", "reject"] });
const draft = T({ id: "t-d", comments: [C({ id: "c-d", review_id: null, draft: true })], draft: true });
const mustFix = T({ id: "t-m", label: "must_fix", state: "open" });

/** registerGates over fakes: the gate's document already read (digest `d1`)
 *  unless `unread`, its threads `threads`, and every command it registers in `handlers`. */
function setup({ threads = [] as ReviewThread[], refusal = () => null as string | null, refresh = async () => true, unread = false, target = undefined as unknown, at = atGate } = {}) {
  const handlers = new Map<string, (...a: unknown[]) => unknown>();
  vi.spyOn(vscode.commands, "registerCommand").mockImplementation(((id: string, cb: (...a: unknown[]) => unknown) => (handlers.set(id, cb), { dispose() {} })) as never);
  const changes: ((ids: string[] | "all") => void)[] = [];
  const store = {
    items: () => [at], item: (id: string) => (id === at.id ? at : undefined),
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
  if (!unread) gates.shown.record(at.id, at.pending_gate!, "d1");
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

  const final = stopped("gate", {
    id: "G-1", title: "Design the caching layer", pending_gate: "final_review", actions: ["approve", "reject"],
    chain_definition: { template_id: "t", nodes: [
      { id: "final_review", kind: "gate", tasks: [], gate_after: "final_review" },
      { id: "merge", kind: "exec", tasks: ["merge/merge"], gate_after: null },
    ] },
  });
  // A gate whose approval merges asks first, naming the item; an ordinary one is one click and says what it did.
  const pushing = { ...final, chain_definition: { template_id: "t", nodes: [
    { id: "final_review", kind: "gate", tasks: [], gate_after: "final_review" },
    { id: "publish", kind: "exec", tasks: ["publish/push"], gate_after: null },
  ] } } as typeof final;
  it.each<[string, typeof atGate, string | undefined, number, string[], string[], ReviewThread[]?]>([
    ["a merging gate asks, Cancel sends nothing", final, undefined, 0, ["Approve and merge Design the caching layer into the target branch?"], []],
    ["a merging gate asks, Approve sends and says so", final, "Approve", 1, ["Approve and merge Design the caching layer into the target branch?"], ["Approved: Design the caching layer · approve final review"]],
    ["a pushing gate asks in its own words", pushing, "Approve", 1, ["Approve and push Design the caching layer?"], ["Approved: Design the caching layer · approve final review"]],
    ["drafts waiting: the send question names the item, the gate in the board's words and the merge", final, "Approve and Send", 1,
      ["Approve final review on Design the caching layer and send 1 draft comment with it? This also merges it into the target branch. No agent acts on comments sent with an approval."], ["Approved: Design the caching layer · approve final review"], [draft]],
    ["an ordinary gate: no question, one toast", atGate, undefined, 1, [], ["Approved: Cache layer · approve spec"]],
  ])("%s", async (_, at, answer, sent, asked, toasts, threads = []) => {
    const { handlers, api } = setup({ at, threads });
    const { ask } = spies();
    ask.mockResolvedValue(answer as never);
    const info = vi.spyOn(vscode.window, "showInformationMessage");
    await handlers.get("kraft.approve")!("G-1", at.pending_gate);
    expect([api.approve.mock.calls.length + api.submitReview.mock.calls.length, ask.mock.calls.map((c) => c[0]), info.mock.calls.map((c) => c[0])]).toEqual([sent, asked, toasts]);
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

  it("leads with the title and says the gate in the board's words", () => {
    const { changes } = setup();
    const toast = vi.spyOn(vscode.window, "showInformationMessage");
    changes.forEach((cb) => cb([atGate.id]));
    expect(toast.mock.calls.map((c) => c[0])).toEqual(["Cache layer: approve spec"]);
  });
});

describe("a stop's notification", () => {
  it.each([
    ["a failure warns", item({ id: "F-1", title: "Cache layer", display_status: "failed", stop: { kind: "failed", node: "implementation", reason: "x", resume_at: null } }), "showWarningMessage", "Cache layer — failed at implementation"],
    ["a question only informs", stopped("question", { id: "Q-1", title: "Cache layer", stop: { kind: "question", node: "implementation", reason: "needs_context: Keep it?", resume_at: null } }), "showInformationMessage", "Cache layer — agent asks: Keep it?"],
  ] as const)("%s", (_, at, level, text) => {
    const { changes } = setup({ at });
    const other = level === "showWarningMessage" ? "showInformationMessage" : "showWarningMessage";
    const toast = vi.spyOn(vscode.window, level);
    const wrong = vi.spyOn(vscode.window, other);
    changes.forEach((cb) => cb([at.id]));
    expect([toast.mock.calls.map((c) => c[0]), wrong.mock.calls.length]).toEqual([[text], 0]);
  });
});

describe("kraft.reject", () => {
  it("asks by title and the gate's words, not the id", async () => {
    const { handlers } = setup();
    const input = vi.spyOn(vscode.window, "showInputBox");
    await handlers.get("kraft.reject")!("G-1", GATE);
    expect(input.mock.calls[0][0]).toMatchObject({ prompt: "Why reject Cache layer at spec?" });
  });
});
