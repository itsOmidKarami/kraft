import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { item, view } from "./fixtures";
import * as api from "./itemDraftApi";
import type { MarkedOp } from "./types";
import { useItemDraft } from "./useItemDraft";
import { setField } from "./view";

vi.mock("./itemDraftApi");
const m = vi.mocked(api);
const ok = <T,>(body: T, status = 200) => ({ status, body });
const ov = (path: string, model: string): MarkedOp => ({ op: "override", path, task_config: { model }, passed: false });

beforeEach(() => {
  vi.resetAllMocks();
  m.getDraft.mockResolvedValue(ok(view()));
});

describe("useItemDraft", () => {
  it("loads the draft and shows the server's answer", async () => {
    m.getDraft.mockResolvedValue(ok(view([ov("verify.main.run", "x")])));
    const { result } = renderHook(() => useItemDraft(item()));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.view?.ops).toHaveLength(1);
  });

  it("builds each edit from the last answer, so two quick edits both land", async () => {
    // The server's list after each PUT: whatever it was sent, marked.
    m.putDraft.mockImplementation(async (_id, ops) => ok(view(ops.map((o) => ({ ...o, passed: false })))));
    const { result } = renderHook(() => useItemDraft(item()));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    await act(async () => {
      void result.current.edit((o) => setField(o, "verify.main.run", "task_config", "model", "a"));
      await result.current.edit((o) => setField(o, "ship", "policy", "budget_usd", 3));
    });
    const sent = m.putDraft.mock.calls.map((c) => c[1]);
    expect(sent[0]).toHaveLength(1);
    expect(sent[1]).toEqual([{ op: "override", path: "verify.main.run", task_config: { model: "a" } }, { op: "override", path: "ship", policy: { budget_usd: 3 } }]);
  });

  it("sends no `passed` and keeps an op the UI does not draw", async () => {
    const foreign: MarkedOp = { op: "skip", path: "ship.main.run", passed: false };
    m.getDraft.mockResolvedValue(ok(view([foreign])));
    m.putDraft.mockResolvedValue(ok(view([foreign])));
    const { result } = renderHook(() => useItemDraft(item()));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    await act(async () => void (await result.current.edit((o) => setField(o, "verify", "policy", "budget_usd", 1))));
    expect(m.putDraft.mock.calls[0][1]).toEqual([{ op: "skip", path: "ship.main.run" }, { op: "override", path: "verify", policy: { budget_usd: 1 } }]);
    expect(JSON.stringify(m.putDraft.mock.calls[0][1])).not.toContain("passed");
  });

  it("sends an empty list when the last op goes", async () => {
    m.getDraft.mockResolvedValue(ok(view([ov("a.b.c", "x")])));
    m.putDraft.mockResolvedValue(ok(view([])));
    const { result } = renderHook(() => useItemDraft(item()));
    await waitFor(() => expect(result.current.view?.ops).toHaveLength(1));
    await act(async () => void (await result.current.edit((o) => setField(o, "a.b.c", "task_config", "model", undefined))));
    expect(m.putDraft).toHaveBeenCalledWith("w1", []);
    expect(result.current.view?.ops).toEqual([]);
  });

  it("turns a refusal's detail into `error` and keeps the view", async () => {
    m.putDraft.mockResolvedValue(ok({ detail: "ops.0.op: bad" }, 422));
    const { result } = renderHook(() => useItemDraft(item()));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    await act(async () => void (await result.current.edit((o) => o)));
    expect(result.current.error).toBe("ops.0.op: bad");
    expect(result.current.view?.ops).toEqual([]);
  });

  it("will not edit before the draft has loaded, rather than send a list it never saw", async () => {
    m.getDraft.mockResolvedValue(ok({ detail: "boom" }, 500));
    const { result } = renderHook(() => useItemDraft(item()));
    await waitFor(() => expect(result.current.status).toBe("error"));
    await act(async () => void (await result.current.edit(() => [])));
    expect(m.putDraft).not.toHaveBeenCalled();
  });

  it("reads the draft again when the item moves to another node, and on window focus", async () => {
    const { rerender } = renderHook(({ cur }) => useItemDraft(item(cur)), { initialProps: { cur: "build" } });
    await waitFor(() => expect(m.getDraft).toHaveBeenCalledTimes(1));
    rerender({ cur: "verify" });
    await waitFor(() => expect(m.getDraft).toHaveBeenCalledTimes(2));
    act(() => void window.dispatchEvent(new Event("focus")));
    await waitFor(() => expect(m.getDraft).toHaveBeenCalledTimes(3));
  });

  it("shows a flag that flipped without sending anything", async () => {
    const first = view([ov("build.main.run", "x")]);
    m.getDraft.mockResolvedValueOnce(ok(first)).mockResolvedValue(ok(view([{ ...(first.ops[0] as MarkedOp), passed: true }])));
    const { result, rerender } = renderHook(({ cur }) => useItemDraft(item(cur)), { initialProps: { cur: "plan" } });
    await waitFor(() => expect(result.current.view?.ops[0].passed).toBe(false));
    rerender({ cur: "build" });
    await waitFor(() => expect(result.current.view?.ops[0].passed).toBe(true));
    expect(m.putDraft).not.toHaveBeenCalled();
  });

  it("fetches nothing for an ended item", async () => {
    const { result } = renderHook(() => useItemDraft(item("ship", "done")));
    expect(result.current.status).toBe("off");
    expect(m.getDraft).not.toHaveBeenCalled();
  });

  it("reads the draft again after apply refuses with 409, and returns the answer", async () => {
    m.applyDraft.mockResolvedValue(ok({ detail: "moved", passed: [0] }, 409));
    const { result } = renderHook(() => useItemDraft(item()));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    let a: unknown;
    await act(async () => void (a = await result.current.apply()));
    expect((a as { status: number }).status).toBe(409);
    expect(m.getDraft).toHaveBeenCalledTimes(2);
  });

  it("takes apply's 200 as the new view, the draft gone", async () => {
    m.getDraft.mockResolvedValue(ok(view([ov("a.b.c", "x")])));
    m.applyDraft.mockResolvedValue(ok(view([])));
    const { result } = renderHook(() => useItemDraft(item()));
    await waitFor(() => expect(result.current.view?.ops).toHaveLength(1));
    await act(async () => void (await result.current.apply()));
    expect(result.current.view?.ops).toEqual([]);
  });
});
