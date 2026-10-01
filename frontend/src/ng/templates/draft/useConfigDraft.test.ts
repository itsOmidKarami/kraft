import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as d from "./draftApi";
import { DEFAULT_VIEW } from "./fixture.default";
import type { DraftView } from "./types";
import { FIELD_PAUSE_MS, TEXT_DEBOUNCE_MS, useConfigDraft } from "./useConfigDraft";

const VIEW = DEFAULT_VIEW;
const answer = <T,>(body: T, status = 200) => Promise.resolve({ status, body });
const withChanges = (n: number): DraftView => ({ ...VIEW, draft: true, result: { ...VIEW.result, changes: Array.from({ length: n }, (_, i) => ({ path: `p${i}`, kind: "change" as const, summary: "x" })) } });

let events = 0;
const onEvent = () => events++;

beforeEach(() => {
  vi.restoreAllMocks();
  events = 0;
  window.addEventListener(d.DRAFTS_CHANGED, onEvent);
  vi.spyOn(d, "getDraft").mockImplementation(() => answer(VIEW));
});
afterEach(() => {
  window.removeEventListener(d.DRAFTS_CHANGED, onEvent);
  vi.useRealTimers();
});

const mount = async () => {
  const h = renderHook(() => useConfigDraft("chains", "default"));
  await waitFor(() => expect(h.result.current.status).toBe("ready"));
  return h;
};

describe("useConfigDraft", () => {
  it("loads the draft view", async () => {
    const h = await mount();
    expect(h.result.current.view?.key).toBe("default");
    expect(d.getDraft).toHaveBeenCalledWith("chains", "default");
  });

  it("is notFound on a 404", async () => {
    vi.mocked(d.getDraft).mockImplementation(() => answer({ detail: "no chain" }, 404) as never);
    const h = renderHook(() => useConfigDraft("chains", "nope"));
    await waitFor(() => expect(h.result.current.status).toBe("notFound"));
  });

  it("replaces the view with an op's answer, and says a draft changed", async () => {
    const h = await mount();
    vi.spyOn(d, "postOps").mockImplementation(() => answer({ ...withChanges(1), ops: [{ op: "add_node" }] }));
    await act(() => h.result.current.ops([{ op: "add_node", at: 0, id: "x", kind: "exec" }]).then(() => {}));
    expect(h.result.current.view?.result.changes).toHaveLength(1);
    expect(events).toBe(1);
  });

  it("sends requests one at a time, in order", async () => {
    const h = await mount();
    const order: string[] = [];
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    vi.spyOn(d, "postOps").mockImplementation(async (_a, _k, ops) => {
      const id = String(ops[0].id);
      order.push(`start ${id}`);
      if (id === "a") await gate;
      order.push(`end ${id}`);
      return { status: 200, body: { ...withChanges(1), ops: [] } };
    });
    let both!: Promise<unknown>;
    act(() => {
      both = Promise.all([h.result.current.ops([{ op: "add_node", id: "a" }]), h.result.current.ops([{ op: "add_node", id: "b" }])]);
    });
    await waitFor(() => expect(order).toEqual(["start a"]));
    release();
    await act(() => both.then(() => {}));
    expect(order).toEqual(["start a", "end a", "start b", "end b"]);
  });

  it("puts a refusal's detail in error, and a preview neither replaces the view nor errors", async () => {
    const h = await mount();
    vi.spyOn(d, "postOps").mockImplementation(() => answer({ detail: "'x' is already taken here", op: 0 }, 422) as never);
    await act(() => h.result.current.ops([{ op: "add_node", id: "x" }]).then(() => {}));
    expect(h.result.current.error).toBe("'x' is already taken here");
    vi.mocked(d.postOps).mockImplementation(() => answer({ ...withChanges(3), ops: [] }));
    act(() => h.result.current.clearError());
    const a = await act(() => h.result.current.ops([{ op: "move", path: "spec", to: 2 }], { preview: true }));
    expect(a.status).toBe(200);
    expect(h.result.current.view?.result.changes).toHaveLength(0);
    expect(events).toBe(1);
  });

  it("debounces YAML text to one PUT, and flushes it before an op", async () => {
    const h = await mount();
    vi.useFakeTimers();
    const put = vi.spyOn(d, "putFile").mockImplementation(() => answer(withChanges(1)));
    const ops = vi.spyOn(d, "postOps").mockImplementation(() => answer({ ...withChanges(2), ops: [] }));
    act(() => {
      h.result.current.text("chains/default.yaml", "a");
      h.result.current.text("chains/default.yaml", "ab");
    });
    await act(async () => void vi.advanceTimersByTime(TEXT_DEBOUNCE_MS - 1));
    expect(put).not.toHaveBeenCalled();
    await act(async () => void vi.advanceTimersByTime(1));
    expect(put).toHaveBeenCalledTimes(1);
    expect(put).toHaveBeenLastCalledWith("chains", "default", "chains/default.yaml", "ab");

    act(() => h.result.current.text("chains/default.yaml", "abc"));
    await act(() => h.result.current.ops([{ op: "add_node", id: "x" }]).then(() => {}));
    expect(put).toHaveBeenCalledTimes(2);
    expect(put.mock.invocationCallOrder[1]).toBeLessThan(ops.mock.invocationCallOrder[0]);
  });

  it("sends a typed field once per pause", async () => {
    const h = await mount();
    vi.useFakeTimers();
    const ops = vi.spyOn(d, "postOps").mockImplementation(() => answer({ ...withChanges(1), ops: [] }));
    act(() => {
      h.result.current.field("spec.main.author", "prompt", "W", true);
      h.result.current.field("spec.main.author", "prompt", "Wr", true);
    });
    await act(async () => void vi.advanceTimersByTime(FIELD_PAUSE_MS - 1));
    expect(ops).not.toHaveBeenCalled();
    await act(async () => void vi.advanceTimersByTime(1));
    expect(ops).toHaveBeenCalledTimes(1);
    expect(ops.mock.calls[0][2]).toEqual([{ op: "set_field", path: "spec.main.author", field: "prompt", value: "Wr" }]);
  });

  it("reloads the draft after a publish instead of taking its answer as the view", async () => {
    const h = await mount();
    vi.spyOn(d, "publish").mockImplementation(() => answer({ published: ["chains/default.yaml"], result: VIEW.result }) as never);
    // The reload hangs, so the view in between is what the page renders.
    vi.mocked(d.getDraft).mockImplementation(() => new Promise(() => {}));
    act(() => void h.result.current.publish());
    await waitFor(() => expect(d.getDraft).toHaveBeenCalledTimes(2));
    expect(h.result.current.view?.files).toEqual(VIEW.files);
  });

  it("says Nothing to undo on the server's 409", async () => {
    const h = await mount();
    vi.spyOn(d, "undo").mockImplementation(() => answer({ detail: "nothing to undo" }, 409) as never);
    await act(() => h.result.current.undo().then(() => {}));
    expect(h.result.current.error).toBe("Nothing to undo");
  });

  it("keeps the 409 body of a stale publish, and the draft", async () => {
    const h = await mount();
    const stale = { detail: "published since this draft began: chains/default.yaml", files: { "chains/default.yaml": { published: "a", draft: "b", diff: "-a\n+b\n" } } };
    vi.spyOn(d, "publish").mockImplementation(() => answer(stale, 409) as never);
    await act(() => h.result.current.publish().then(() => {}));
    expect(h.result.current.stale).toEqual(stale);
    expect(h.result.current.error).toBeNull();
    expect(h.result.current.view?.key).toBe("default");
  });

  it("refetches on window focus when nothing is queued", async () => {
    const h = await mount();
    vi.mocked(d.getDraft).mockImplementation(() => answer(withChanges(4)));
    await act(async () => void window.dispatchEvent(new Event("focus")));
    await waitFor(() => expect(h.result.current.view?.result.changes).toHaveLength(4));
  });

  it("keeps the last resolved steps of a node when the draft stops resolving", async () => {
    const h = await mount();
    vi.spyOn(d, "postOps").mockImplementation(() => answer({ ...VIEW, draft: true, result: { ...VIEW.result, resolved: null }, ops: [] }));
    await act(() => h.result.current.ops([{ op: "add_node", id: "x" }]).then(() => {}));
    expect(h.result.current.view?.result.resolved).toBeNull();
    expect(h.result.current.resolvedNode("verification")?.steps?.map((s) => s.id)).toEqual(["tests", "review"]);
  });
});
