import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { KraftEvent } from "../../types";
import { stubFetch } from "./testkit";
import { RECENT, useEventLog, useEvents } from "./useEvents";

afterEach(() => vi.unstubAllGlobals());
const ev = (seq: number): KraftEvent => ({ seq, work_item_id: "w1", type: "escalation_message", payload: {}, created_at: "t" });

describe("useEventLog", () => {
  it("reads the whole log once, then only what came after the newest event it holds, never adding one twice", async () => {
    const answers: Record<string, [number, unknown]> = { "GET /work-items/w1/events": [200, [ev(1), ev(4)]] };
    stubFetch(answers);
    const urls = () => vi.mocked(globalThis.fetch).mock.calls.map((c) => String(c[0]));
    const { result, rerender } = renderHook(({ v }) => useEventLog("w1", v), { initialProps: { v: "1" } });
    await waitFor(() => expect(result.current?.map((e) => e.seq)).toEqual([1, 4]));
    // The stub ignores after_seq: an event already held comes back and is not added again.
    answers["GET /work-items/w1/events"] = [200, [ev(4), ev(9)]];
    rerender({ v: "2" });
    await waitFor(() => expect(result.current?.map((e) => e.seq)).toEqual([1, 4, 9]));
    expect(urls()).toEqual(["/api/work-items/w1/events?after_seq=0", "/api/work-items/w1/events?after_seq=4"]);
  });

  it("reads nothing while off", async () => {
    stubFetch();
    const { result } = renderHook(() => useEventLog("w1", "1", false));
    await act(async () => {});
    expect(result.current).toBeNull();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});

describe("useEvents", () => {
  it("reads the last page once, then tops it up after the newest event it holds, keeping the last RECENT", async () => {
    const page = Array.from({ length: RECENT }, (_, k) => ev(k + 1));
    const answers: Record<string, [number, unknown]> = { "GET /work-items/w1/events": [200, page] };
    stubFetch(answers);
    const urls = () => vi.mocked(globalThis.fetch).mock.calls.map((c) => String(c[0]));
    const { result, rerender } = renderHook(({ v }) => useEvents("w1", v), { initialProps: { v: "1" } });
    await waitFor(() => expect(result.current).toHaveLength(RECENT));
    answers["GET /work-items/w1/events"] = [200, [ev(RECENT), ev(RECENT + 1), ev(RECENT + 2)]];
    rerender({ v: "2" });
    await waitFor(() => expect(result.current.at(-1)?.seq).toBe(RECENT + 2));
    expect(result.current).toHaveLength(RECENT);
    expect(result.current[0].seq).toBe(3);
    expect(urls()).toEqual([`/api/work-items/w1/events?before_seq=${2 ** 31 - 1}&limit=${RECENT}`, `/api/work-items/w1/events?after_seq=${RECENT}`]);
  });
});
