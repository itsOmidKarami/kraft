import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../store";
import type { CompareTarget, KraftEvent, ReviewThread } from "../../types";
import type { EventType } from "../../types/vocab.generated";
import * as http from "../http";
import { COALESCE_MS } from "../item/useItem";
import { parsePatch } from "./patch";
import { THREADS_POLL_MS, useCompare, useExpanded, useThreads } from "./useReview";

const ev = (work_item_id: string, seq: number, type: EventType): KraftEvent => ({ seq, work_item_id, type, payload: {}, created_at: "t" });
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

describe("useExpanded", () => {
  it("reads a file for its thread once a comparison, however often the threads are read again", async () => {
    // The read comes back cut at the size cap, so nothing is kept of it: only the memory of having tried.
    vi.mocked(http.request).mockResolvedValue({ status: 200, body: { diff: "", truncated: true } });
    const patch = new Map(parsePatch("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -4,3 +4,4 @@\n d = 4\n+e = 5\n f = 6\n g = 7\n").map((f) => [f.path, f]));
    const thread = { id: "t1", file_path: "a.py", side: "new", start_line: 2, end_line: 2 } as ReviewThread;
    const { rerender } = renderHook((p: { threads: ReviewThread[] }) => useExpanded("w1", "base", "latest", false, patch, p.threads), { initialProps: { threads: [thread] } });
    await act(async () => {});
    rerender({ threads: [thread] });
    await act(async () => {});
    expect(calls("context=")).toEqual(["/work-items/w1/compare?from=base&to=latest&file=a.py&context=1000000"]);
  });

  it("reads a thread's file for the comparison that replaced the one being read", async () => {
    const PATCH = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -4,3 +4,4 @@\n d = 4\n+e = 5\n f = 6\n g = 7\n";
    const WHOLE = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,6 +1,7 @@\n a = 1\n b = 2\n c = 3\n d = 4\n+e = 5\n f = 6\n g = 7\n";
    const answers: ((r: { status: number; body: unknown }) => void)[] = [];
    vi.mocked(http.request).mockImplementation(() => new Promise((answer) => answers.push(answer)));
    const patchOf = () => new Map(parsePatch(PATCH).map((f) => [f.path, f]));
    const threads = [{ id: "t1", file_path: "a.py", side: "new", start_line: 2, end_line: 2 } as ReviewThread];
    const { result, rerender } = renderHook((p: { patch: ReturnType<typeof patchOf> }) => useExpanded("w1", "base", "latest", false, p.patch, threads), { initialProps: { patch: patchOf() } });
    // The comparison is read again (a new commit) while the first one's file is still being read.
    rerender({ patch: patchOf() });
    expect(answers).toHaveLength(2);
    await act(async () => answers.forEach((answer) => answer({ status: 200, body: { diff: WHOLE, truncated: false } })));
    expect(result.current.patch.get("a.py")!.hunks[0].lines.map((l) => l.text)).toEqual(["a = 1", "b = 2", "c = 3", "d = 4", "e = 5", "f = 6", "g = 7"]);
  });
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
  // R9b-15: a draft from `kraft item comment` writes no event, and an open review
  // page kept saying "1 pending" over a second one.
  it("reads again on coming back into view, and every so often while shown, with no event", async () => {
    renderHook(() => useThreads("w1"));
    expect(calls("/threads")).toHaveLength(1);
    act(() => void window.dispatchEvent(new Event("focus")));
    expect(calls("/threads")).toHaveLength(2);
    await act(async () => void vi.advanceTimersByTime(THREADS_POLL_MS));
    expect(calls("/threads")).toHaveLength(3);
  });

  it("keeps the same threads, not a new array, when a read brings nothing new (review L6)", async () => {
    vi.mocked(http.request).mockImplementation(async () => ({ status: 200, body: [{ id: "t1" }] }));
    const { result } = renderHook(() => useThreads("w1"));
    await act(async () => {});
    const first = result.current.state === "ready" ? result.current.data : null;
    expect(first).toEqual([{ id: "t1" }]);
    await act(async () => void vi.advanceTimersByTime(THREADS_POLL_MS));
    expect(calls("/threads")).toHaveLength(2);
    expect(result.current.state === "ready" && result.current.data).toBe(first);
  });

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
