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
});
