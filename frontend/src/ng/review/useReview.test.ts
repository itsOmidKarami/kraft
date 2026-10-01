import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../store";
import type { CompareTarget, KraftEvent } from "../../types";
import * as http from "../http";
import { COALESCE_MS } from "../item/useItem";
import { useCompare, useThreads } from "./useReview";

const ev = (work_item_id: string, seq: number, type: string): KraftEvent => ({ seq, work_item_id, type, payload: {}, created_at: "t" });
const calls = (part: string) => vi.mocked(http.request).mock.calls.filter(([p]) => String(p).includes(part)).map(([p]) => String(p));

beforeEach(() => {
  vi.useFakeTimers();
  useStore.setState({ eventsByItem: {}, workItems: {} });
  vi.spyOn(http, "request").mockResolvedValue({ status: 200, body: [] });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useCompare", () => {
  it("reads again when a target, the whitespace flag or HEAD changes, and only then", async () => {
    const { rerender } = renderHook((p: { from: CompareTarget; ws: boolean; head: string }) => useCompare("w1", p.from, "latest", p.ws, p.head), {
      initialProps: { from: "base", ws: false, head: "h1" },
    });
    rerender({ from: "base", ws: false, head: "h1" });
    rerender({ from: "last_review", ws: false, head: "h1" });
    rerender({ from: "last_review", ws: true, head: "h1" });
    rerender({ from: "last_review", ws: true, head: "h2" });
    await act(async () => {});
    expect(calls("/compare")).toEqual([
      "/work-items/w1/compare?from=base&to=latest",
      "/work-items/w1/compare?from=last_review&to=latest",
      "/work-items/w1/compare?from=last_review&to=latest&ignore_whitespace=1",
      "/work-items/w1/compare?from=last_review&to=latest&ignore_whitespace=1",
    ]);
  });

  it("keeps a refusal's status and words (the picker's stale-URL case)", async () => {
    vi.mocked(http.request).mockResolvedValue({ status: 404, body: { detail: "no review has been submitted for this gate" } });
    const { result } = renderHook(() => useCompare("w1", "last_review", "latest", false, "h"));
    await act(async () => {});
    expect(result.current).toEqual({ state: "error", status: 404, error: "no review has been submitted for this gate" });
  });
});

describe("useThreads", () => {
  it("reads once, then once per burst of this item's thread events, never for others", async () => {
    renderHook(() => useThreads("w1"));
    expect(calls("/threads")).toHaveLength(1);
    for (let i = 1; i <= 4; i++) act(() => useStore.getState().applyEvent(ev("w1", i, i % 2 ? "thread_updated" : "reply_agent_wrote")));
    act(() => useStore.getState().applyEvent(ev("w2", 9, "thread_updated")));
    act(() => useStore.getState().applyEvent(ev("w1", 10, "node_completed")));
    await act(async () => void vi.advanceTimersByTime(COALESCE_MS + 1));
    expect(calls("/threads")).toHaveLength(2);
    act(() => useStore.getState().applyEvent(ev("w2", 11, "review_submitted")));
    act(() => useStore.getState().applyEvent(ev("w1", 12, "node_started")));
    await act(async () => void vi.advanceTimersByTime(COALESCE_MS + 1));
    expect(calls("/threads")).toHaveLength(2);
  });
});
