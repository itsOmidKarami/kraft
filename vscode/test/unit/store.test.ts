import { describe, expect, it, vi } from "vitest";
import { Store, type Socket } from "../../src/core/store";

function fakeSocket() {
  const s = { msg: (_: string) => {}, close: () => {}, closed: false } as any;
  const socket: Socket = {
    onMessage: (cb) => (s.msg = cb),
    onClose: (cb) => (s.close = cb),
    close: () => (s.closed = true),
  };
  return { s, socket };
}

const item = (id: string, extra = {}) => ({ id, title: id, status: "active", current_node_id: "n", ...extra }) as any;

function setup() {
  const sockets: ReturnType<typeof fakeSocket>[] = [];
  const urls: string[] = [];
  const api = {
    listItems: vi.fn(async () => ({ items: [item("A")], cursor: 7 })),
    getItem: vi.fn(async (id: string) => item(id, { status: "needs_human", pending_gate: "spec" })),
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
  const store = new Store(api, factory);
  return { api, store, sockets, urls };
}

describe("Store", () => {
  it("loads the board, then subscribes after its cursor", async () => {
    const { store, urls } = setup();
    await store.start();
    expect(store.items().map((i) => i.id)).toEqual(["A"]);
    expect(urls).toEqual(["ws://x/api/ws/events?after_seq=7"]);
    expect(store.connected).toBe(true);
  });

  it("re-fetches only the item an event names", async () => {
    const { store, sockets, api } = setup();
    await store.start();
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
    api.listItems.mockRejectedValue(new Error("down"));
    sockets[0].s.close();
    expect(store.connected).toBe(false);
    for (let i = 0; i < 8; i++) await vi.advanceTimersByTimeAsync(30_000);
    const delays = (store as any).lastDelays as number[];
    expect(Math.max(...delays)).toBe(30_000);
    api.listItems.mockResolvedValue({ items: [item("A"), item("B")], cursor: 9 });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(store.items().map((i) => i.id)).toEqual(["A", "B"]);
    expect(urls.at(-1)).toBe("ws://x/api/ws/events?after_seq=9");
  });

  it("drops an item that no longer exists", async () => {
    const { store, sockets, api } = setup();
    await store.start();
    api.getItem.mockRejectedValue(Object.assign(new Error("gone"), { status: 404 }));
    sockets[0].s.msg(JSON.stringify({ seq: 8, work_item_id: "A", type: "x", payload: {}, created_at: "" }));
    await vi.runAllTimersAsync();
    expect(store.item("A")).toBeUndefined();
  });
});
