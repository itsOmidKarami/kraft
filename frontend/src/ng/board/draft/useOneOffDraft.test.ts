import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_VIEW } from "../../templates/draft/fixture.default";
import { useOneOffDraft } from "./useOneOffDraft";

type Call = { text: string; ops: unknown[]; preview?: boolean };
let calls: Call[];
let reply: (c: Call) => { status: number; body: unknown };
const view = (text: string, more: object = {}) => ({ ...DEFAULT_VIEW, key: "one-off", text, digest: "d".repeat(64), custom: [], custom_nodes: [], nodes: [{ id: "implementation" }], ...more });

beforeEach(() => {
  calls = [];
  reply = (c) => ({ status: 200, body: view(c.ops.length ? `${c.text}+` : c.text) });
  vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
    const c = JSON.parse(String(init.body)) as Call;
    calls.push(c);
    const r = reply(c);
    return new Response(JSON.stringify(r.body), { status: r.status });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const mount = async (text = "id: one-off\n") => {
  const onText = vi.fn();
  const h = renderHook(() => useOneOffDraft(text, onText));
  await waitFor(() => expect(h.result.current.status).toBe("ready"));
  return { h, onText };
};

describe("useOneOffDraft", () => {
  it("reads once on mount, with no ops", async () => {
    await mount();
    expect(calls).toEqual([{ text: "id: one-off\n", ops: [] }]);
  });

  it("sends each queued op the text the previous answer returned", async () => {
    const { h } = await mount();
    await act(async () => {
      void h.result.current.ops([{ op: "a" }]);
      await h.result.current.ops([{ op: "b" }]);
    });
    expect(calls.slice(1).map((c) => c.text)).toEqual(["id: one-off\n", "id: one-off\n+"]);
  });

  it("undo puts back the previous text", async () => {
    const { h, onText } = await mount();
    await act(async () => { await h.result.current.ops([{ op: "a" }]); });
    await act(async () => { await h.result.current.undo(); });
    expect(onText).toHaveBeenLastCalledWith("id: one-off\n");
  });

  it("a preview never calls onText nor adds an undo step", async () => {
    const { h, onText } = await mount();
    onText.mockClear();
    await act(async () => { await h.result.current.ops([{ op: "a" }], { preview: true }); });
    expect(onText).not.toHaveBeenCalled();
    expect(calls.at(-1)).toMatchObject({ preview: true });
    const a = await act(async () => h.result.current.undo());
    expect(a).toMatchObject({ status: 409 });
  });

  it("a 422 is the error, and the text stays", async () => {
    const { h, onText } = await mount();
    onText.mockClear();
    reply = () => ({ status: 422, body: { detail: "no such node" } });
    await act(async () => { await h.result.current.ops([{ op: "a" }]); });
    expect(h.result.current.error).toBe("no such node");
    expect(onText).not.toHaveBeenCalled();
  });

  it("is valid only with no syntax error and no problems", async () => {
    const { h } = await mount();
    expect(h.result.current.valid).toBe(true);
    reply = (c) => ({ status: 200, body: view(c.text, { result: { ...DEFAULT_VIEW.result, yaml_error: { file: "chains/one-off.yaml", line: 1, col: 1, message: "bad" } }, digest: null }) });
    await act(async () => { await h.result.current.ops([{ op: "a" }]); });
    expect(h.result.current.valid).toBe(false);
  });

  it("flush sends typed text now and resolves when the queue is empty", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { h } = await mount();
    h.result.current.text("chains/one-off.yaml", "id: one-off\nextends: x\n");
    await act(async () => { await h.result.current.flush(); });
    expect(calls.at(-1)).toEqual({ text: "id: one-off\nextends: x\n", ops: [] });
    vi.useRealTimers();
  });

  it("an answer that lands after the chain was left changes nothing", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onText = vi.fn();
    const h = renderHook(({ t }) => useOneOffDraft(t, onText), { initialProps: { t: "id: one-off\n" } });
    await waitFor(() => expect(h.result.current.status).toBe("ready"));
    onText.mockClear();
    h.result.current.text("chains/one-off.yaml", "id: one-off\nextends: x\n");
    h.rerender({ t: "" });
    await act(async () => { await vi.advanceTimersByTimeAsync(600); });
    expect(onText).not.toHaveBeenCalled();
    expect(calls).toHaveLength(1);
    vi.useRealTimers();
  });
});
