import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReviewThread } from "../../src/core/api";
import { COALESCE_MS, preloadsDrafts, reviewGone, ThreadCache } from "../../src/core/threadCache";
import { item, stopped } from "./fixtures/item";
import { T } from "./fixtures/thread";

/** A `listThreads` whose answers the test hands out one request at a time. */
function controlled() {
  const pending: ((t: ReviewThread[]) => void)[] = [];
  const listThreads = vi.fn(() => new Promise<ReviewThread[]>((r) => pending.push(r)));
  const answer = async (t: ReviewThread[]) => {
    await vi.waitFor(() => expect(pending.length).toBeGreaterThan(0));
    pending.shift()!(t);
  };
  return { listThreads, answer };
}

const one = [T({ id: "a" })];
const two = [T({ id: "a" }), T({ id: "b" })];

describe("ThreadCache.load", () => {
  it("a load while one is in flight runs once more, not once per call", async () => {
    const api = controlled();
    const cache = new ThreadCache(api);
    const calls = [cache.load("K-1"), cache.load("K-1"), cache.load("K-1")];
    await api.answer(one);
    await api.answer(two);
    expect(await Promise.all(calls)).toEqual([two, two, two]);
    expect(api.listThreads).toHaveBeenCalledTimes(2);
  });

  it("an older answer never replaces a newer one", async () => {
    const api = controlled();
    const cache = new ThreadCache(api);
    const first = cache.load("K-1");
    const second = cache.load("K-1");
    await api.answer(one);
    await api.answer(two);
    await Promise.all([first, second]);
    expect(cache.get("K-1")).toEqual(two);
  });

  it("rejects with the last answer's error, and loads again on the next call", async () => {
    const listThreads = vi.fn().mockRejectedValueOnce(new Error("down")).mockResolvedValueOnce(one);
    const cache = new ThreadCache({ listThreads });
    await expect(cache.load("K-1")).rejects.toThrow("down");
    expect(await cache.load("K-1")).toEqual(one);
  });
});

describe("ThreadCache.poke", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const loadedCache = async () => {
    const listThreads = vi.fn(async () => one);
    const cache = new ThreadCache({ listThreads });
    await cache.load("K-1");
    listThreads.mockClear();
    return { cache, listThreads };
  };

  it("pokes within the window coalesce", async () => {
    const { cache, listThreads } = await loadedCache();
    cache.poke("K-1");
    cache.poke("K-1");
    cache.poke("K-1");
    await vi.advanceTimersByTimeAsync(COALESCE_MS);
    expect(listThreads).toHaveBeenCalledTimes(1);
  });

  it("a poke for an item never loaded does nothing", async () => {
    const { cache, listThreads } = await loadedCache();
    cache.poke("K-2");
    await vi.advanceTimersByTimeAsync(COALESCE_MS);
    expect(listThreads).not.toHaveBeenCalled();
  });

  it.each([
    ["thread_updated", 1],
    ["review_submitted", 1],
    ["rewind_requested", 1],
    ["rewind_cancelled", 1],
    ["reply_agent_wrote", 1],
    ["reply_agent_failed", 1],
    ["gate_requested", 0],
    ["thread_updatedx", 0],
    ["reply_agent_", 0],
  ])("onEvent %s loads %i time(s)", async (type, n) => {
    const { cache, listThreads } = await loadedCache();
    cache.onEvent({ type, work_item_id: "K-1" });
    await vi.advanceTimersByTimeAsync(COALESCE_MS);
    expect(listThreads).toHaveBeenCalledTimes(n);
  });

  it("forget drops the item and its pending poke", async () => {
    const { cache, listThreads } = await loadedCache();
    cache.poke("K-1");
    cache.forget("K-1");
    await vi.advanceTimersByTimeAsync(COALESCE_MS);
    expect(listThreads).not.toHaveBeenCalled();
    expect(cache.loaded()).toEqual([]);
  });
});

it("onChange fires only on a different answer", async () => {
  const listThreads = vi.fn().mockResolvedValueOnce(one).mockResolvedValueOnce(structuredClone(one)).mockResolvedValueOnce(two);
  const cache = new ThreadCache({ listThreads });
  const seen: string[] = [];
  cache.onChange((id) => seen.push(id));
  await cache.load("K-1");
  await cache.load("K-1");
  await cache.load("K-1");
  expect(seen).toEqual(["K-1", "K-1"]);
});

it("an item forgotten while its load is in flight fires no onChange", async () => {
  const api = controlled();
  const cache = new ThreadCache(api);
  const seen: string[] = [];
  cache.onChange((id) => seen.push(id));
  const load = cache.load("K-1");
  cache.forget("K-1");
  await api.answer(one);
  await load;
  expect(seen).toEqual([]);
  expect(cache.loaded()).toEqual([]);
});

it.each([
  { id: "gone from the board", it: undefined, gone: true },
  { id: "archived", it: item({ display_status: "archived" }), gone: true },
  { id: "abandoned", it: item({ status: "abandoned", display_status: "cancelled" }), gone: true },
  { id: "done, still listed", it: item({ status: "completed", display_status: "done" }), gone: false },
  { id: "running", it: item(), gone: false },
])("reviewGone: $id", ({ it: i, gone }) => expect(reviewGone(i)).toBe(gone));

it.each([
  { id: "at a gate", it: stopped("gate", { pending_gate: "spec", actions: ["approve", "reject"] }), want: true },
  { id: "at a gate, before `actions`", it: stopped("gate", { pending_gate: "spec", actions: undefined }), want: true },
  // Gateless, a review is Request changes: a resume or retry carries it, so its drafts may wait there.
  { id: "paused", it: item({ display_status: "paused", actions: ["resume", "steer", "cancel"] }), want: true },
  // Filed paused (`kraft item create`'s default) and never started: no diff to have drafts on, and a backlog of them would cost a GET each.
  { id: "never started", it: item({ display_status: "paused", current_node_id: null, actions: ["resume", "cancel"] }), want: false },
  { id: "stopped, retry only", it: stopped("cap", { actions: ["retry", "cancel"] }), want: true },
  { id: "running: no door that stops on you", it: item({ actions: ["pause", "steer", "cancel"] }), want: false },
  { id: "done", it: item({ status: "completed", display_status: "done", actions: ["archive"] }), want: false },
  { id: "abandoned, though resumable", it: item({ status: "abandoned", display_status: "cancelled", actions: ["resume"] }), want: false },
])("preloadsDrafts: $id", ({ it: i, want }) => expect(preloadsDrafts(i)).toBe(want));
