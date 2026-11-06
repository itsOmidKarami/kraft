import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { stubFetch } from "./testkit";
import { POLL_MS } from "./useLogLines";
import { detail } from "./testkit";
import { runningCounts, subagentCount } from "./subagents";
import { useRunningSubagents, useSessionSubagents, useSubagentDetail } from "./useSubagents";

const running = { id: "a1", description: "Explore", status: "running" };
const answer = (list: unknown[]) => ({ "GET /work-items/w1/sessions/s1/subagents": [200, { session_id: "s1", status: "running", subagents: list }] }) as never;

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
const visibility = (state: "visible" | "hidden") => Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); visibility("visible"); });

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

  it("shows nothing of the old session once the session changes, even when the new read fails", async () => {
    const answers = answer([running]);
    stubFetch(answers);
    const { result, rerender } = renderHook(({ id }) => useSessionSubagents("w1", id, false), { initialProps: { id: "s1" } });
    await vi.waitFor(() => expect(result.current).toEqual([running]));
    rerender({ id: "s2" });
    await vi.waitFor(() => expect(result.current).toBeNull());
  });

  it("reads nothing for a session that is no agent's", async () => {
    const calls = stubFetch(answer([running]));
    const { result } = renderHook(() => useSessionSubagents("w1", "s1", true, false));
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 2); });
    expect(calls.length).toBe(0);
    expect(result.current).toBeNull();
  });
});

describe("polling", () => {
  const session = { id: "s1", status: "running", harness: "claude" } as never;
  it.each<{ id: string; use: () => unknown }>([
    { id: "one session's list", use: () => useSessionSubagents("w1", "s1", true) },
    { id: "the running sessions' lists", use: () => useRunningSubagents(detail({ worker_sessions: [session] })) },
  ])("reads $id again only while the page is visible", async ({ use }) => {
    const calls = stubFetch(answer([running]));
    visibility("hidden");
    renderHook(use);
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 3); });
    expect(calls.length).toBe(1);
    visibility("visible");
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS); });
    expect(calls.length).toBe(2);
  });
});

describe("runningCounts", () => {
  it("counts only what runs, per session", () => {
    const of = (...status: string[]) => status.map((s, i) => ({ id: String(i), description: "d", status: s })) as never;
    expect(runningCounts({ s1: of("running", "completed", "running"), s2: of("cut_off", "failed") })).toEqual({ s1: 2, s2: 0 });
  });
});

describe("useSubagentDetail", () => {
  it("reads once opened, and again on a retry only after it failed", async () => {
    const path = "GET /work-items/w1/sessions/s1/subagents/a1";
    const answers: Record<string, [number, unknown]> = { [path]: [500, {}] };
    const calls = stubFetch(answers);
    const { result } = renderHook(() => useSubagentDetail("w1", "s1", "a1"));
    act(() => result.current.load());
    await vi.waitFor(() => expect(result.current.detail).toBe("failed"));
    answers[path] = [200, { ...running, prompt: "p", answer: null }];
    act(() => result.current.load());
    await vi.waitFor(() => expect(result.current.detail).toMatchObject({ prompt: "p" }));
    act(() => result.current.load());
    expect(calls.length).toBe(2);
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
