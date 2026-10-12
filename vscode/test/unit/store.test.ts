import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../src/core/api";
import { handshakeError, Store, type ConnError, type Socket } from "../../src/core/store";
import { item } from "./fixtures/item";

function fakeSocket() {
  const s = { open: () => {}, msg: (_: string) => {}, close: (_?: ConnError) => {}, closed: false } as any;
  const socket: Socket = {
    onOpen: (cb) => (s.open = cb),
    onMessage: (cb) => (s.msg = cb),
    onClose: (cb) => (s.close = cb),
    close: () => (s.closed = true),
  };
  return { s, socket };
}

function setup() {
  const sockets: ReturnType<typeof fakeSocket>[] = [];
  const urls: string[] = [];
  const api = {
    listItems: vi.fn(async () => ({ items: [item({ id: "A" })], cursor: 7 })),
    getItem: vi.fn(async (id: string) => item({ id, status: "needs_human", pending_gate: "spec" })),
    wsUrl: (n: number) => `ws://x/api/ws/events?after_seq=${n}`,
    headers: () => ({}),
  } as any;
  const factory = (url: string) => {
    urls.push(url);
    const f = fakeSocket();
    sockets.push(f);
    return f.socket;
  };
  vi.useFakeTimers();
  const log = vi.fn();
  const store = new Store(api, factory, log);
  return { api, store, sockets, urls, log };
}

const event = (seq: number, work_item_id: string, type: string) =>
  JSON.stringify({ seq, work_item_id, type, payload: {}, created_at: "" });

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => ((resolve = res), (reject = rej)));
  return { promise, resolve, reject };
}

const delays = (store: Store) => (store as any).lastDelays as number[];
const timers = () => vi.getTimerCount();

describe("Store", () => {
  it("loads the board, then subscribes after its cursor, and is connected only once the socket opens", async () => {
    const { store, urls, sockets } = setup();
    await store.start();
    expect(store.items().map((i) => i.id)).toEqual(["A"]);
    expect(urls).toEqual(["ws://x/api/ws/events?after_seq=7"]);
    expect(store.connected).toBe(false);
    sockets[0].s.open();
    expect(store.connected).toBe(true);
  });

  it("re-fetches only the item an event names", async () => {
    const { store, sockets, api } = setup();
    await store.start();
    sockets[0].s.open();
    const changed: unknown[] = [];
    store.onChange((ids) => changed.push(ids));
    sockets[0].s.msg(JSON.stringify({ seq: 8, work_item_id: "A", type: "gate_requested", payload: { gate: "spec" }, created_at: "" }));
    await vi.runAllTimersAsync();
    expect(api.getItem).toHaveBeenCalledWith("A");
    expect(store.item("A")?.pending_gate).toBe("spec");
    expect(changed).toContainEqual(["A"]);
  });

  it("reconnects with capped backoff and re-fetches everything", async () => {
    const { store, sockets, api, urls } = setup();
    await store.start();
    sockets[0].s.open();
    api.listItems.mockRejectedValue(new Error("down"));
    sockets[0].s.close();
    expect(store.connected).toBe(false);
    for (let i = 0; i < 8; i++) await vi.advanceTimersByTimeAsync(30_000);
    expect(Math.max(...delays(store))).toBe(30_000);
    api.listItems.mockResolvedValue({ items: [item({ id: "A" }), item({ id: "B" })], cursor: 9 });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(store.items().map((i) => i.id)).toEqual(["A", "B"]);
    expect(urls.at(-1)).toBe("ws://x/api/ws/events?after_seq=9");
  });

  it("a refused handshake backs off and never reports connected", async () => {
    const { store, sockets, api } = setup();
    const states: boolean[] = [];
    store.onConnection((v) => states.push(v));
    await store.start();
    sockets[0].s.close({ status: 401, detail: "Unexpected server response: 401" });
    await vi.advanceTimersByTimeAsync(1_000);
    sockets[1].s.close({ status: 401, detail: "Unexpected server response: 401" });
    await vi.advanceTimersByTimeAsync(2_000);
    expect(states).not.toContain(true);
    expect(delays(store)).toEqual([1_000, 2_000]);
    expect(store.lastError).toEqual({ status: 401, detail: "Unexpected server response: 401" });
    expect(api.listItems).toHaveBeenCalledTimes(3);
  });

  it("a socket that closes before it opens, with no error, is still a failure", async () => {
    const { store, sockets } = setup();
    await store.start();
    sockets[0].s.close();
    expect(store.lastError).toEqual({ status: 0, detail: "socket closed before open" });
    expect(delays(store)).toEqual([1_000]);
  });

  it("open then close reconnects at 1s", async () => {
    const { store, sockets } = setup();
    await store.start();
    sockets[0].s.close();
    await vi.advanceTimersByTimeAsync(1_000);
    sockets[1].s.open();
    sockets[1].s.close();
    expect(delays(store)).toEqual([1_000, 1_000]);
  });

  it("a 401 list call sets lastError and backs off", async () => {
    const { store, api, urls } = setup();
    api.listItems.mockRejectedValue(new ApiError(401, "invalid token"));
    const errs: unknown[] = [];
    store.onError((e) => errs.push(e));
    await store.start();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(store.lastError).toEqual({ status: 401, detail: "invalid token" });
    expect(errs).toEqual([{ status: 401, detail: "invalid token" }]);
    expect(urls).toEqual([]);
    expect(delays(store)).toEqual([1_000, 2_000]);
  });

  it("lastError follows the latest failure and clears on open", async () => {
    const { store, sockets, api } = setup();
    const errs: unknown[] = [];
    store.onError((e) => errs.push(e));
    await store.start();
    sockets[0].s.close({ status: 401, detail: "Unexpected server response: 401" });
    await vi.advanceTimersByTimeAsync(1_000);
    sockets[1].s.open();
    expect(store.lastError).toBeNull();
    api.listItems.mockRejectedValue(new ApiError(0, "not reachable"));
    sockets[1].s.close();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(store.lastError).toEqual({ status: 0, detail: "not reachable" });
    expect(errs).toEqual([{ status: 401, detail: "Unexpected server response: 401" }, null, { status: 0, detail: "not reachable" }]);
  });

  it("retryNow while a retry is scheduled leaves one socket and no timer", async () => {
    const { store, sockets } = setup();
    await store.start();
    sockets[0].s.close({ status: 0, detail: "ECONNREFUSED" });
    expect(timers()).toBe(1);
    store.retryNow();
    await vi.advanceTimersByTimeAsync(0);
    expect(timers()).toBe(0);
    expect(sockets).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(sockets).toHaveLength(2);
    sockets[1].s.open();
    expect(store.connected).toBe(true);
  });

  it("retryNow closes the live socket and ignores its close", async () => {
    const { store, sockets } = setup();
    await store.start();
    sockets[0].s.open();
    store.retryNow();
    await vi.advanceTimersByTimeAsync(0);
    expect(sockets[0].s.closed).toBe(true);
    sockets[0].s.close();
    expect(timers()).toBe(0);
    expect(sockets).toHaveLength(2);
  });

  it("retryNow while the list call is in flight, then it rejects: one timer, one socket", async () => {
    const { store, sockets, api } = setup();
    const first = deferred<never>();
    api.listItems.mockImplementationOnce(() => first.promise);
    const started = store.start();
    store.retryNow();
    await vi.advanceTimersByTimeAsync(0);
    expect(sockets).toHaveLength(1);
    first.reject(new ApiError(0, "not reachable"));
    await started;
    expect(timers()).toBe(0);
    expect(delays(store)).toEqual([]);
    expect(store.lastError).toBeNull();
    sockets[0].s.close();
    expect(timers()).toBe(1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(sockets).toHaveLength(2);
  });

  it.each([
    ["resolves", (d: ReturnType<typeof deferred<any>>) => d.resolve({ items: [item({ id: "A" })], cursor: 7 })],
    ["rejects", (d: ReturnType<typeof deferred<any>>) => d.reject(new ApiError(0, "not reachable"))],
  ])("stop during a pending list call opens nothing when it %s", async (_, settle) => {
    const { store, api, urls, log } = setup();
    const d = deferred<any>();
    api.listItems.mockImplementationOnce(() => d.promise);
    const started = store.start();
    store.stop();
    settle(d);
    await started;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(urls).toEqual([]);
    expect(delays(store)).toEqual([]);
    expect(log).not.toHaveBeenCalled();
  });

  it("a socket that opens or closes after stop() connects nothing, logs nothing and schedules nothing", async () => {
    const { store, sockets, log } = setup();
    await store.start();
    store.stop();
    sockets[0].s.open();
    expect(store.connected).toBe(false);
    sockets[0].s.close({ status: 0, detail: "x" });
    expect(sockets[0].s.closed).toBe(true);
    expect(log).not.toHaveBeenCalled();
    expect(timers()).toBe(0);
  });

  it("logs connects, disconnects and retries with their delay", async () => {
    const { store, sockets, api, log } = setup();
    await store.start();
    sockets[0].s.open();
    api.listItems.mockRejectedValue(new ApiError(0, "not reachable"));
    sockets[0].s.close();
    await vi.advanceTimersByTimeAsync(1_000);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(log.mock.calls).toEqual([
      ["info", "connected to ws://x/api/ws/events"],
      ["warn", "disconnected"],
      ["info", "retrying in 1s"],
      ["warn", "list failed: 0 not reachable"],
      ["info", "retrying in 2s"],
      // The same failure again is not logged twice.
      ["info", "retrying in 4s"],
    ]);
  });

  it.each([
    ["Unexpected server response: 403", { status: 403, detail: "Unexpected server response: 403" }],
    ["connect ECONNREFUSED 127.0.0.1:8765", { status: 0, detail: "connect ECONNREFUSED 127.0.0.1:8765" }],
  ])("handshakeError(%s)", (msg, want) => expect(handshakeError(new Error(msg))).toEqual(want));

  it("forwards a non-moving event without re-reading", async () => {
    const { store, sockets, api } = setup();
    await store.start();
    sockets[0].s.open();
    const seen: string[] = [];
    store.onEvent((e) => seen.push(e.type));
    sockets[0].s.msg(event(8, "A", "mr_labels_set"));
    await vi.runAllTimersAsync();
    expect(seen).toEqual(["mr_labels_set"]);
    expect(api.getItem).not.toHaveBeenCalled();
  });

  it("re-reads an event for an unknown item and adds it", async () => {
    const { store, sockets, api } = setup();
    await store.start();
    sockets[0].s.open();
    sockets[0].s.msg(event(8, "B", "mr_labels_set"));
    await vi.runAllTimersAsync();
    expect(api.getItem).toHaveBeenCalledWith("B");
    expect(store.item("B")).toBeDefined();
  });

  it("logs and ignores a malformed frame", async () => {
    const { store, sockets, api, log } = setup();
    await store.start();
    sockets[0].s.open();
    sockets[0].s.msg("{nope");
    sockets[0].s.msg(event(8, "A", "work_item_needs_human"));
    await vi.runAllTimersAsync();
    expect(log).toHaveBeenCalledWith("warn", "ignored a malformed event: {nope");
    expect(api.getItem).toHaveBeenCalledTimes(1);
  });

  it("coalesces a burst of re-reads for one item", async () => {
    const { store, sockets, api } = setup();
    let release!: () => void;
    api.getItem.mockImplementationOnce((id: string) => new Promise((r) => (release = () => r(item({ id })))));
    await store.start();
    sockets[0].s.open();
    for (const seq of [8, 9, 10]) sockets[0].s.msg(event(seq, "A", "work_item_needs_human"));
    await vi.advanceTimersByTimeAsync(0);
    expect(api.getItem).toHaveBeenCalledTimes(1);
    release();
    await vi.runAllTimersAsync();
    expect(api.getItem).toHaveBeenCalledTimes(2);
  });

  it("drops an item that no longer exists, and a queued re-read does not bring it back", async () => {
    const { store, sockets, api, log } = setup();
    await store.start();
    sockets[0].s.open();
    api.getItem.mockRejectedValueOnce(Object.assign(new Error("gone"), { status: 404 }));
    sockets[0].s.msg(event(8, "A", "work_item_cancelled"));
    sockets[0].s.msg(event(9, "A", "work_item_cancelled"));
    await vi.runAllTimersAsync();
    expect(store.item("A")).toBeUndefined();
    expect(api.getItem).toHaveBeenCalledTimes(1);
    expect(log).toHaveBeenCalledWith("info", "re-read A: no longer on the server");
  });

  it("logs a failed re-read instead of throwing", async () => {
    const { store, sockets, api, log } = setup();
    await store.start();
    sockets[0].s.open();
    api.getItem.mockRejectedValueOnce(new ApiError(500, "db locked"));
    sockets[0].s.msg(event(8, "A", "work_item_cancelled"));
    await vi.runAllTimersAsync();
    expect(log).toHaveBeenCalledWith("warn", "re-read A failed: db locked");
  });
});
