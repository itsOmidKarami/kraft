import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { stubFetch } from "./testkit";
import { POLL_MS } from "./useLogLines";
import { detail } from "./testkit";
import { subagentCount } from "./subagents";
import { useRunningSubagents, useSessionSubagents } from "./useSubagents";

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

describe("useRunningSubagents", () => {
  const session = (id: string, over: Record<string, unknown> = {}) => ({ id, status: "running", harness: "claude", ...over }) as never;
  const route = (id: string, list: unknown[]) => [`GET /work-items/w1/sessions/${id}/subagents`, [200, { session_id: id, status: "running", subagents: list }]] as const;

  it("polls each running agent session, keyed by its id, and no other", async () => {
    const calls = stubFetch(Object.fromEntries([route("s1", [running]), route("s2", [{ ...running, id: "a2" }]), route("s3", [running]), route("s4", [running])]) as never);
    const item = detail({ worker_sessions: [session("s1"), session("s2"), session("s3", { harness: null }), session("s4", { status: "done" })] });
    const { result } = renderHook(() => useRunningSubagents(item));
    await vi.waitFor(() => expect(Object.keys(result.current).sort()).toEqual(["s1", "s2"]));
    expect(result.current.s2[0].id).toBe("a2");
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS); });
    expect(calls.filter((c) => c.path.endsWith("/subagents")).map((c) => c.path.split("/")[4]).sort()).toEqual(["s1", "s1", "s2", "s2"]);
  });

  it("drops a session from the map once it stops running", async () => {
    stubFetch(Object.fromEntries([route("s1", [running])]) as never);
    let item = detail({ worker_sessions: [session("s1")] });
    const { result, rerender } = renderHook(() => useRunningSubagents(item));
    await vi.waitFor(() => expect(Object.keys(result.current)).toEqual(["s1"]));
    item = detail({ worker_sessions: [session("s1", { status: "done" })] });
    rerender();
    expect(result.current).toEqual({});
  });
});
