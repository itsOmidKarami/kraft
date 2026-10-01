import { afterEach, describe, expect, it, vi } from "vitest";
import { applyDraft, discardDraft, getDraft, putDraft } from "./itemDraftApi";

const stub = (status: number, body: unknown) => {
  const f = vi.fn(async () => new Response(status === 204 ? null : JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", f);
  return f;
};
afterEach(() => vi.unstubAllGlobals());

describe("itemDraftApi", () => {
  it("sends the whole list in a PUT and the right verb for each route", async () => {
    const f = stub(200, { ops: [] });
    await getDraft("w 1");
    await putDraft("w 1", [{ op: "skip", path: "a.b.c" }]);
    await applyDraft("w 1");
    stub(204, null);
    await discardDraft("w 1");
    const calls = f.mock.calls as unknown as [string, RequestInit][];
    expect(calls.map(([u, i]) => `${i?.method ?? "GET"} ${u}`)).toEqual(["GET /api/work-items/w%201/draft", "PUT /api/work-items/w%201/draft", "POST /api/work-items/w%201/draft/apply"]);
    expect(JSON.parse(String(calls[1][1].body))).toEqual({ ops: [{ op: "skip", path: "a.b.c" }] });
  });
  it("hands a 409's passed indexes and a 422's problems to the caller whole", async () => {
    stub(409, { detail: "moved past", passed: [0, 2] });
    expect((await applyDraft("w1")).body).toEqual({ detail: "moved past", passed: [0, 2] });
    stub(422, { detail: "1 problem(s)", problems: [{ op: 0, message: "no" }] });
    expect(await applyDraft("w1")).toEqual({ status: 422, body: { detail: "1 problem(s)", problems: [{ op: 0, message: "no" }] } });
  });
  it("answers a discard's 204 with no body", async () => {
    stub(204, null);
    expect(await discardDraft("w1")).toEqual({ status: 204, body: undefined });
  });
});
