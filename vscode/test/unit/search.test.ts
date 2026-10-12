import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as vscode from "vscode";
import type { SearchResult } from "../../../frontend/src/types/documents";
import { ApiError, type Api } from "../../src/core/api";
import { latest, searchPicks } from "../../src/core/search";
import { openReadOnlyDoc } from "../../src/docs";
import { registerSearch } from "../../src/search";

// The editor tab itself is VS Code's; what matters here is what it is asked to show.
vi.mock("../../src/docs", () => ({ openReadOnlyDoc: vi.fn(async () => {}) }));

const link = (work_item_id: string | null) => ({ work_item_id, node_id: null, hook_point: null, worker_session_id: null });
const hit = (over: Partial<SearchResult> = {}): SearchResult => ({
  id: "d1", repo: "/home/me/kraft", source_kind: "artifact", kind: "spec", title: "Search design", path: "p",
  snippet: "s", score: 1, links: [], ...over,
});

describe("searchPicks", () => {
  it.each([
    ["takes the item from the first link that names one", hit({ links: [link(null), link("K-1")] }), { itemId: "K-1", docId: "d1" }],
    ["keeps the document of a hit linked to no item", hit({ id: "d9", links: [] }), { itemId: null, docId: "d9" }],
    ["shows the snippet without its match brackets", hit({ snippet: "the [gate] opens" }), { detail: "the gate opens" }],
    ["describes it by kind and repo name", hit(), { label: "Search design", description: "spec · kraft" }],
    ["falls back to the source kind", hit({ kind: null, source_kind: "session_summary" }), { description: "session_summary · kraft" }],
    ["titles a session summary by its snippet, as the web UI does", hit({ title: "", kind: null, snippet: "[Fixed] the flaky gate test" }),
      { label: "Fixed the flaky gate test" }],
  ])("%s", (_, r, want) => expect(searchPicks([r])[0]).toMatchObject(want));
});

describe("latest", () => {
  it("leaves only the newest request current", () => {
    const begin = latest();
    const first = begin();
    expect(first()).toBe(true);
    const second = begin();
    expect([first(), second()]).toEqual([false, true]);
  });
});

describe("Kraft: Search…", () => {
  type Handlers = { value?: (v: string) => void; accept?: () => void; hide?: () => void };
  const setUp = (search: (q: string) => Promise<unknown>, reveal = vi.fn(async () => true), document = vi.fn()) => {
    const on: Handlers = {};
    const qp = {
      items: [] as unknown[], selectedItems: [] as unknown[], title: "", placeholder: "", busy: false,
      onDidChangeValue: (cb: Handlers["value"]) => (on.value = cb), onDidAccept: (cb: Handlers["accept"]) => (on.accept = cb),
      onDidHide: (cb: Handlers["hide"]) => (on.hide = cb), show: vi.fn(), hide: vi.fn(() => on.hide!()), dispose: vi.fn(),
    };
    vi.spyOn(vscode.window, "createQuickPick").mockReturnValue(qp as never);
    let run: () => void = () => {};
    vi.spyOn(vscode.commands, "registerCommand").mockImplementation(((_id: string, cb: () => void) => ((run = cb), { dispose() {} })) as never);
    const exec = vi.spyOn(vscode.commands, "executeCommand").mockResolvedValue(undefined);
    const api = { search: vi.fn(search), document } as unknown as Api;
    registerSearch({ subscriptions: [] } as never, api, reveal);
    run();
    return { on, qp, api, exec, reveal };
  };
  const answer = (...results: SearchResult[]) => ({ query: "q", mode: "hybrid", results });

  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("waits for typing to pause, skips a blank query, and keeps only the latest answer", async () => {
    const replies: ((v: unknown) => void)[] = [];
    const { on, qp, api } = setUp(() => new Promise((r) => replies.push(r)));
    on.value!("   ");
    await vi.advanceTimersByTimeAsync(250);
    on.value!("ga");
    await vi.advanceTimersByTimeAsync(100);
    on.value!("gate");
    await vi.advanceTimersByTimeAsync(250);
    expect(vi.mocked(api.search).mock.calls).toEqual([["gate"]]);
    on.value!("gates");
    await vi.advanceTimersByTimeAsync(250);
    replies[1](answer(hit({ id: "new" })));
    replies[0](answer(hit({ id: "old" })));
    await vi.advanceTimersByTimeAsync(0);
    // `alwaysShow`: the server chose these, so the quick pick's own filter must not hide one.
    expect((qp.items as { pick: { docId: string }; alwaysShow?: boolean }[]).map((i) => [i.pick.docId, i.alwaysShow])).toEqual([["new", true]]);
  });

  it("stays busy until the latest request's answer lands", async () => {
    const replies: ((v: unknown) => void)[] = [];
    const { on, qp } = setUp(() => new Promise((r) => replies.push(r)));
    on.value!("gate");
    await vi.advanceTimersByTimeAsync(250);
    expect(qp.busy).toBe(true);
    on.value!("gates");
    await vi.advanceTimersByTimeAsync(250);
    replies[0](answer());
    await vi.advanceTimersByTimeAsync(0);
    expect(qp.busy).toBe(true); // a stale answer leaves the newer request spinning
    replies[1](answer());
    await vi.advanceTimersByTimeAsync(0);
    expect(qp.busy).toBe(false);
  });

  it.each([
    ["the server's note", async () => ({ ...answer(hit()), note: "text search only" }), "Kraft: Search — text search only"],
    ["a refusal's detail", async () => { throw new ApiError(422, "bad search query"); }, "Kraft: Search — bad search query"],
    ["an empty answer", async () => answer(), "Kraft: Search — nothing found"],
  ])("puts %s in the title", async (_, search, want) => {
    const { on, qp } = setUp(search);
    on.value!("x");
    await vi.advanceTimersByTimeAsync(250);
    expect(qp.title).toBe(want);
  });

  it("sends nothing once the pick is hidden", async () => {
    const { on, qp, api } = setUp(async () => answer());
    on.value!("gate");
    qp.hide();
    await vi.advanceTimersByTimeAsync(500);
    expect(api.search).not.toHaveBeenCalled();
    expect(qp.dispose).toHaveBeenCalled();
  });

  it.each([
    ["selects an item the board shows", true, []],
    ["opens one the board does not show in the web UI", false, [["kraft.openInWebUi", { item: { id: "K-1" } }]]],
  ])("%s", async (_, shown, want) => {
    const { on, qp, exec, reveal } = setUp(async () => answer(), vi.fn(async () => shown));
    qp.selectedItems = [{ pick: searchPicks([hit({ links: [link("K-1")] })])[0] }];
    on.accept!();
    await vi.advanceTimersByTimeAsync(0);
    expect(reveal).toHaveBeenCalledWith("K-1");
    expect(exec.mock.calls).toEqual(want);
  });

  it("opens a document with no item under the document's own title", async () => {
    const doc = { id: "d9", title: "", content: "Fixed the flaky gate test\nmore", kind: null, links: [] };
    const { on, qp } = setUp(async () => answer(), undefined, vi.fn().mockResolvedValue(doc));
    qp.selectedItems = [{ pick: searchPicks([hit({ id: "d9", title: "", snippet: "the [gate] test" })])[0] }];
    on.accept!();
    await vi.advanceTimersByTimeAsync(0);
    expect(vi.mocked(openReadOnlyDoc).mock.calls).toEqual([["doc/d9", "Fixed the flaky gate test", doc.content]]);
  });

  it("says why a document with no item cannot be opened", async () => {
    const shown = vi.spyOn(vscode.window, "showErrorMessage");
    const { on, qp } = setUp(async () => answer(), undefined, vi.fn().mockRejectedValue(new ApiError(404, "document d9 not found")));
    qp.selectedItems = [{ pick: searchPicks([hit({ id: "d9" })])[0] }];
    on.accept!();
    await vi.advanceTimersByTimeAsync(0);
    expect(shown).toHaveBeenCalledWith("document d9 not found");
  });
});
