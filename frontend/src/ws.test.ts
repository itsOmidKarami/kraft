import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "./store";
import { connectEvents } from "./ws";

class FakeWS {
  static instances: FakeWS[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  close = vi.fn();
  constructor(url: string) {
    this.url = url;
    FakeWS.instances.push(this);
  }
}

beforeEach(() => {
  FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS as unknown as typeof WebSocket);
  vi.useFakeTimers();
  useStore.setState({ lastSeq: 3, connection: "connecting" } as never);
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("connectEvents", () => {
  it("connects with the current lastSeq and marks the connection open", () => {
    connectEvents();
    expect(FakeWS.instances[0].url).toContain("after_seq=3");
    FakeWS.instances[0].onopen!();
    expect(useStore.getState().connection).toBe("open");
  });

  it("applies inbound frames to the store", () => {
    connectEvents();
    FakeWS.instances[0].onmessage!({
      data: JSON.stringify({ seq: 9, work_item_id: "w1", type: "x", payload: {}, created_at: "t" }),
    });
    expect(useStore.getState().lastSeq).toBe(9);
  });

  it("reconnects after close using the updated lastSeq and escalating backoff", () => {
    connectEvents();
    useStore.setState({ lastSeq: 20 } as never);
    FakeWS.instances[0].onclose!();
    expect(useStore.getState().connection).toBe("reconnecting");
    vi.advanceTimersByTime(1000);
    expect(FakeWS.instances[1].url).toContain("after_seq=20");
    // second failure -> longer wait
    FakeWS.instances[1].onclose!();
    vi.advanceTimersByTime(1999);
    expect(FakeWS.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeWS.instances).toHaveLength(3);
  });

  it("error+close on the same socket only schedules one reconnect", () => {
    connectEvents();
    // A broken socket fires 'error' then 'close'; both must not each retry.
    FakeWS.instances[0].onerror!();
    FakeWS.instances[0].onclose?.(); // detached by the first retry -> no-op
    vi.advanceTimersByTime(1000);
    expect(FakeWS.instances).toHaveLength(2);
    // attempt incremented once, so the next rung is 2000ms, not 5000ms.
    FakeWS.instances[1].onclose!();
    vi.advanceTimersByTime(2000);
    expect(FakeWS.instances).toHaveLength(3);
  });

  it("the disposer stops reconnection", () => {
    const stop = connectEvents();
    stop();
    FakeWS.instances[0].onclose!();
    vi.advanceTimersByTime(60000);
    expect(FakeWS.instances).toHaveLength(1);
  });
});
