import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _resetLaterForTests, sendLater, UNDO_WINDOW_MS } from "./later";

const hide = () => window.dispatchEvent(new Event("pagehide"));

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  _resetLaterForTests();
  vi.useRealTimers();
});

describe("sendLater", () => {
  it("sends once after the window, not before", () => {
    const send = vi.fn(async () => {});
    sendLater(send, UNDO_WINDOW_MS);
    vi.advanceTimersByTime(UNDO_WINDOW_MS - 1);
    expect(send).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    vi.advanceTimersByTime(UNDO_WINDOW_MS * 2);
    expect(send.mock.calls).toEqual([[{ keepalive: false }]]);
  });

  it("sends nothing when undone inside the window, and says whether it caught it", () => {
    const send = vi.fn(async () => {});
    const early = sendLater(send, 1000);
    expect(early.undo()).toBe(true);
    vi.advanceTimersByTime(5000);
    expect(send).not.toHaveBeenCalled();
    const late = sendLater(send, 1000);
    vi.advanceTimersByTime(1000);
    expect(late.undo()).toBe(false);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("sends a pending action on pagehide with keepalive, once, and the timer then adds nothing", () => {
    const send = vi.fn(async () => {});
    const later = sendLater(send, 1000);
    hide();
    hide();
    vi.advanceTimersByTime(5000);
    expect(send.mock.calls).toEqual([[{ keepalive: true }]]);
    expect(later.undo()).toBe(false);
  });

  it("sends every pending action on pagehide, and not one already undone", () => {
    const a = vi.fn(async () => {});
    const b = vi.fn(async () => {});
    const c = vi.fn(async () => {});
    sendLater(a, 1000);
    sendLater(b, 2000);
    sendLater(c, 2000).undo();
    hide();
    expect([a.mock.calls, b.mock.calls, c.mock.calls]).toEqual([[[{ keepalive: true }]], [[{ keepalive: true }]], []]);
  });
});
