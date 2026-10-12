import { afterEach, expect, it, vi } from "vitest";
import type { WorkItem } from "../../src/core/api";
import type { Store } from "../../src/core/store";
import type { ThreadCache } from "../../src/core/threadCache";
import { registerComments } from "../../src/review/comments";
import { item, stopped } from "./fixtures/item";

afterEach(() => vi.restoreAllMocks());

// The board's rows whose drafts registerComments loads before any review opens (preloadsDrafts).
it.each<[string, WorkItem, boolean]>([
  ["at a gate", stopped("gate", { id: "X", pending_gate: "spec", actions: ["approve"] }), true],
  ["paused, gateless: its web UI drafts too", item({ id: "X", display_status: "paused", actions: ["resume"] }), true],
  ["running", item({ id: "X", actions: ["pause"] }), false],
])("a row %s: preloaded %s", (_, row, loaded) => {
  const changes: ((ids: string[] | "all") => void)[] = [];
  const store = {
    items: () => [row], item: () => row,
    onChange: (cb: (ids: string[] | "all") => void) => (changes.push(cb), () => {}), onEvent: () => () => {},
  } as unknown as Store;
  const cache = {
    get: () => undefined, load: vi.fn(async () => []), loaded: () => [], onChange: () => () => {}, onEvent: () => {}, poke: () => {},
  } as unknown as ThreadCache;
  const diff = { onOpened: () => () => {}, opened: new Map() };
  registerComments({ subscriptions: [] } as never, store, {} as never, () => null, diff as never, cache);
  changes.forEach((cb) => cb("all"));
  expect(vi.mocked(cache.load).mock.calls).toEqual(loaded ? [["X"]] : []);
});
