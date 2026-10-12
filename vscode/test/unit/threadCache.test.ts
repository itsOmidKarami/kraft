import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReviewThread } from "../../src/core/api";
import { COALESCE_MS, ThreadCache } from "../../src/core/threadCache";
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
