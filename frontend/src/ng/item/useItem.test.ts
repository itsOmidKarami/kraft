import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { useStore } from "../../store";
import type { KraftEvent } from "../../types";
import { COALESCE_MS, useItem } from "./useItem";

const ev = (work_item_id: string, seq: number): KraftEvent => ({ seq, work_item_id, type: "node_completed", payload: {}, created_at: "t" });

beforeEach(() => {
  vi.useFakeTimers();
  useStore.setState({ eventsByItem: {}, workItems: {} });
  vi.spyOn(api, "getWorkItem").mockResolvedValue({ id: "w1", worker_sessions: [] } as never);
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useItem", () => {
  it("reads the item once, then once per burst of its own events, never for another item's", async () => {
    renderHook(() => useItem("w1"));
    expect(api.getWorkItem).toHaveBeenCalledTimes(1);
    // Each event its own render, as the socket delivers them.
    for (let i = 1; i <= 5; i++) act(() => useStore.getState().applyEvent(ev("w1", i)));
    act(() => useStore.getState().applyEvent(ev("w2", 9)));
    await act(async () => void vi.advanceTimersByTime(COALESCE_MS + 1));
    expect(api.getWorkItem).toHaveBeenCalledTimes(2);
    act(() => useStore.getState().applyEvent(ev("w2", 10)));
    await act(async () => void vi.advanceTimersByTime(COALESCE_MS + 1));
    expect(api.getWorkItem).toHaveBeenCalledTimes(2);
  });

  it("keeps the version when a read brings nothing new, and moves it on a new session status or event", async () => {
    const at = (status: string) => ({ id: "w1", updated_at: "u1", worker_sessions: [{ id: "s1", status }] }) as never;
    vi.mocked(api.getWorkItem).mockResolvedValue(at("running"));
    const { result } = renderHook(() => useItem("w1"));
    await act(async () => {});
    const first = result.current.state === "ready" ? result.current.version : "";
    await act(async () => result.current.reload());
    expect(result.current.state === "ready" && result.current.version).toBe(first);
    vi.mocked(api.getWorkItem).mockResolvedValue(at("failed"));
    await act(async () => result.current.reload());
    const failed = result.current.state === "ready" ? result.current.version : "";
    expect(failed).not.toBe(first);
    act(() => useStore.getState().applyEvent(ev("w1", 7)));
    await act(async () => void vi.advanceTimersByTime(COALESCE_MS + 1));
    expect(result.current.state === "ready" && result.current.version).not.toBe(failed);
  });
});
