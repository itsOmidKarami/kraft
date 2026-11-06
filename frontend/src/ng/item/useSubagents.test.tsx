import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { stubFetch } from "./testkit";
import { POLL_MS } from "./useLogLines";
import { subagentCount, useSessionSubagents } from "./useSubagents";

const running = { id: "a1", description: "Explore", status: "running" };
const answer = (list: unknown[]) => ({ "GET /work-items/w1/sessions/s1/subagents": [200, { session_id: "s1", status: "running", subagents: list }] }) as never;

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("useSessionSubagents", () => {
  it("reads again every few seconds while the session runs", async () => {
    const calls = stubFetch(answer([running]));
    const { result } = renderHook(() => useSessionSubagents("w1", "s1", true));
    await vi.waitFor(() => expect(result.current).toEqual([running]));
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 2); });
    expect(calls.length).toBe(3);
  });

  it("reads once for a session that is no longer running", async () => {
    const calls = stubFetch(answer([running]));
    const { result } = renderHook(() => useSessionSubagents("w1", "s1", false));
    await vi.waitFor(() => expect(result.current).toEqual([running]));
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 3); });
    expect(calls.length).toBe(1);
  });

  it("reads nothing for a session that is no agent's", async () => {
    const calls = stubFetch(answer([running]));
    const { result } = renderHook(() => useSessionSubagents("w1", "s1", true, false));
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 2); });
    expect(calls.length).toBe(0);
    expect(result.current).toBeNull();
  });
});

describe("subagentCount", () => {
  it("separates what runs, what finished and what was cut off", () => {
    const of = (...status: string[]) => status.map((s, i) => ({ id: String(i), description: "d", status: s })) as never;
    expect(subagentCount(of("running", "completed", "failed", "killed", "cut_off"))).toEqual({ running: 1, finished: 3, cutOff: 1 });
  });
});
