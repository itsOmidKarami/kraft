import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stubFetch } from "../../item/testkit";
import { LOG_POLL_MS, useLog } from "./useLog";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("useLog", () => {
  it("drops the no-log note once a pending attempt's log appears on a later read", async () => {
    vi.useFakeTimers();
    const answers: Record<string, [number, unknown]> = { "GET /worker-sessions/s1/log": [404, { detail: "no log" }] };
    stubFetch(answers);
    const { result } = renderHook(() => useLog("s1", true));
    await act(async () => void (await vi.advanceTimersByTimeAsync(10)));
    expect(result.current.error).toBe("No log for this attempt yet.");
    answers["GET /worker-sessions/s1/log"] = [200, { lines: [{ n: 1, t: "0:01", src: "agent", text: "started" }] }];
    await act(async () => void (await vi.advanceTimersByTimeAsync(LOG_POLL_MS + 10)));
    expect(result.current.lines).toHaveLength(1);
    expect(result.current.error).toBeNull();
  });
});
