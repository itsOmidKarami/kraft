import { describe, expect, it, vi } from "vitest";
import { Api, ApiError, REQUEST_TIMEOUT, SLOW_TIMEOUT, type ApiFailure } from "../../src/core/api";

function fakeFetch(status: number, body: unknown) {
  return vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

describe("Api", () => {
  it("prefixes /api and sends the bearer token", async () => {
    const f = fakeFetch(200, { items: [], cursor: 3 });
    const api = new Api("http://h:1", "tok", f as any);
    expect(await api.listItems()).toEqual({ items: [], cursor: 3 });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("http://h:1/api/work-items?include_abandoned=true");
    expect((init as RequestInit).headers).toMatchObject({ Authorization: "Bearer tok" });
  });

  it("never claims to be an MCP client", async () => {
    const f = fakeFetch(200, {});
    await new Api("http://h:1", "tok", f as any).pause("W-1");
    expect(JSON.stringify(f.mock.calls[0][1])).not.toMatch(/x-kraft-client/i);
  });

  it("raises the daemon's detail verbatim", async () => {
    const api = new Api("http://h:1", undefined, fakeFetch(409, { detail: "gate 'plan' is not pending" }) as any);
    await expect(api.reject("W-1", "plan", "no")).rejects.toMatchObject({
      status: 409,
      detail: "gate 'plan' is not pending",
    });
  });

  it("posts approve with the digest only when given", async () => {
    const f = fakeFetch(200, {});
    const api = new Api("http://h:1", undefined, f as any);
    await api.approve("W-1", "revise", "d1");
    await api.approve("W-1", "spec");
    expect(JSON.parse((f.mock.calls[0][1] as RequestInit).body as string)).toEqual({ digest: "d1" });
    expect(JSON.parse((f.mock.calls[1][1] as RequestInit).body as string)).toEqual({});
  });

  it("encodes a gate path segment", async () => {
    const f = fakeFetch(200, {});
    await new Api("http://h:1", undefined, f as any).reject("W-1", "review gate", "n");
    expect(f.mock.calls[0][0]).toBe("http://h:1/api/work-items/W-1/gates/review%20gate/reject");
  });

  it.each([
    ["raiseBudget", (a: Api) => a.raiseBudget("W-1", 12.5), "/budget/raise", { budget_usd: 12.5 }],
    ["raiseBudget to no cap", (a: Api) => a.raiseBudget("W-1", null), "/budget/raise", { budget_usd: null }],
    ["unblock", (a: Api) => a.unblock("W-1"), "/unblock", undefined],
    ["complete", (a: Api) => a.complete("W-1", "landed by hand"), "/complete", { reason: "landed by hand" }],
    ["restore", (a: Api) => a.restore("W-1"), "/restore", undefined],
  ])("posts %s", async (_, call, path, body) => {
    const f = fakeFetch(200, {});
    await call(new Api("http://h:1", undefined, f as any));
    const [url, init] = f.mock.calls[0];
    expect(url).toBe(`http://h:1/api/work-items/W-1${path}`);
    expect((init as RequestInit).method).toBe("POST");
    expect((init as RequestInit).body === undefined ? undefined : JSON.parse((init as RequestInit).body as string)).toEqual(body);
  });

  const filed = { title: "T", description: "", repo: "/r", autostart: false };
  it.each([
    ["previews with dry_run=1 and the create body", (a: Api) => a.preview(filed), "/work-items?dry_run=1", filed],
    ["files to /work-items", (a: Api) => a.create(filed), "/work-items", filed],
    ["abandons by id", (a: Api) => a.abandon("W 1"), "/work-items/W%201/abandon", {}],
  ])("%s", async (_, call, path, body) => {
    const f = fakeFetch(200, {});
    await call(new Api("http://h:1", undefined, f as any));
    const [url, init] = f.mock.calls[0];
    expect(url).toBe(`http://h:1/api${path}`);
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual(body);
  });

  it("maps a missing artifact to null", async () => {
    expect(await new Api("http://h:1", undefined, fakeFetch(404, { detail: "none" }) as any).getArtifact("W-1")).toBeNull();
  });

  it("builds the websocket URL", () => {
    expect(new Api("http://h:1", undefined).wsUrl(42)).toBe("ws://h:1/api/ws/events?after_seq=42");
  });

  it("wraps a network failure as ApiError status 0", async () => {
    const f = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    await expect(new Api("http://h:1", undefined, f as any).health()).rejects.toBeInstanceOf(ApiError);
  });

  it("reads what applies from GET /apply", async () => {
    const body = { restart: [], reload: [{ id: "disk:policy.yaml", file: "policy.yaml", text: "policy.yaml changed on disk since it was loaded" }], managed: false };
    const f = fakeFetch(200, body);
    expect(await new Api("http://h:1", undefined, f as any).pending()).toEqual(body);
    expect(f.mock.calls[0][0]).toBe("http://h:1/api/apply");
    expect((f.mock.calls[0][1] as RequestInit).method).toBe("GET");
  });

  it.each([
    ["202 restarting", 202, { restarting: true }],
    ["409 not managed", 409, { detail: "started from a terminal: restart it there" }],
    ["500 could not start", 500, { detail: "could not start the restart: boom" }],
  ])("POST /apply/restart: %s", async (_id, status, body) => {
    const f = fakeFetch(status, body);
    const call = new Api("http://h:1", undefined, f as any).restart();
    if (status === 202) await expect(call).resolves.toEqual({ restarting: true });
    else await expect(call).rejects.toMatchObject({ status, detail: (body as { detail: string }).detail });
    expect(f.mock.calls[0][0]).toBe("http://h:1/api/apply/restart");
    expect((f.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });

  it.each([
    { id: "list", call: (a: Api) => a.listThreads("K 1"), method: "GET", url: "/work-items/K%201/threads", body: undefined },
    {
      id: "create", call: (a: Api) => a.createThread("K-1", { body: "b", file_path: "a.py", side: "old", start_line: 2, end_line: 3, label: "must_fix" }),
      method: "POST", url: "/work-items/K-1/threads", body: { body: "b", file_path: "a.py", side: "old", start_line: 2, end_line: 3, label: "must_fix" },
    },
    { id: "relabel only", call: (a: Api) => a.patchThread("t1", { label: null }), method: "PATCH", url: "/threads/t1", body: { label: null } },
    { id: "reply", call: (a: Api) => a.addComment("t1", "r"), method: "POST", url: "/threads/t1/comments", body: { body: "r" } },
    {
      id: "edit reply keeps suggestion", call: (a: Api) => a.patchComment("c1", { body: "x", suggestion: { start_line: 1, end_line: 1, replacement: "y" } }),
      method: "PATCH", url: "/comments/c1", body: { body: "x", suggestion: { start_line: 1, end_line: 1, replacement: "y" } },
    },
    { id: "delete thread", call: (a: Api) => a.deleteThread("t1"), method: "DELETE", url: "/threads/t1", body: undefined },
    { id: "delete reply", call: (a: Api) => a.deleteComment("c1"), method: "DELETE", url: "/comments/c1", body: undefined },
    { id: "resolve", call: (a: Api) => a.resolveThread("t1"), method: "POST", url: "/threads/t1/resolve", body: undefined },
    { id: "reopen", call: (a: Api) => a.reopenThread("t1"), method: "POST", url: "/threads/t1/reopen", body: undefined },
    {
      id: "at a gate", call: (a: Api) => a.submitReview("K-1", "spec/x", { outcome: "approve", digest: "d" }),
      method: "POST", url: "/work-items/K-1/gates/spec%2Fx/review", body: { outcome: "approve", digest: "d" },
    },
    {
      id: "gateless", call: (a: Api) => a.submitReview("K-1", null, { outcome: "request_changes", summary: "s" }),
      method: "POST", url: "/work-items/K-1/review", body: { outcome: "request_changes", summary: "s" },
    },
  ])("threads and reviews: $id", async ({ call, method, url, body }) => {
    const f = fakeFetch(200, {});
    await call(new Api("http://h:1", undefined, f as any));
    const [u, init] = f.mock.calls[0];
    expect(u).toBe(`http://h:1/api${url}`);
    expect((init as RequestInit).method).toBe(method);
    const sent = (init as RequestInit).body;
    expect(sent === undefined ? undefined : JSON.parse(sent as string)).toEqual(body);
  });

  it("resolves a 204 with no body to undefined", async () => {
    const f = vi.fn(async () => new Response(null, { status: 204 }));
    expect(await new Api("http://h:1", undefined, f as any).deleteThread("t1")).toBeUndefined();
  });

  it("reports a refused call to onError", async () => {
    const api = new Api("http://h:1", undefined, fakeFetch(401, { detail: "bad token" }) as any);
    const seen: ApiFailure[] = [];
    api.onError = (f) => seen.push(f);
    await expect(api.listItems()).rejects.toBeInstanceOf(ApiError);
    expect(seen).toEqual([{ method: "GET", path: "/work-items?include_abandoned=true", status: 401, detail: "bad token" }]);
  });

  it("reports an unreachable server as status 0", async () => {
    const api = new Api("http://h:1", undefined, vi.fn(async () => { throw new Error("ECONNREFUSED"); }) as any);
    const seen: ApiFailure[] = [];
    api.onError = (f) => seen.push(f);
    await expect(api.pause("W-1")).rejects.toMatchObject({ status: 0 });
    expect(seen).toEqual([{ method: "POST", path: "/work-items/W-1/pause", status: 0, detail: "Kraft is not reachable at http://h:1: ECONNREFUSED" }]);
  });

  it("reports nothing for a call that succeeds", async () => {
    const api = new Api("http://h:1", undefined, fakeFetch(200, {}) as any);
    const seen: ApiFailure[] = [];
    api.onError = (f) => seen.push(f);
    await api.health();
    expect(seen).toEqual([]);
  });
});

describe("Api timeout", () => {
  it("gives up on a daemon that never answers, as status 0", async () => {
    // The fetch rejects only when the request's own signal aborts, as fetch does.
    const f = vi.fn((_url: string, init?: RequestInit) =>
      new Promise<Response>((_, reject) => init!.signal!.addEventListener("abort", () => reject(init!.signal!.reason))));
    const api = new Api("http://h:1", undefined, f as any, { fast: 1_000, slow: 60_000 });
    const seen: ApiFailure[] = [];
    api.onError = (e) => seen.push(e);
    const t0 = Date.now();
    await expect(api.health()).rejects.toMatchObject({ status: 0, detail: "Kraft did not answer within 1s" });
    expect(Date.now() - t0).toBeGreaterThanOrEqual(900);
    expect(seen).toEqual([{ method: "GET", path: "/health", status: 0, detail: "Kraft did not answer within 1s" }]);
  });

  it("waits 10s by default, 60s for a slow call", () => expect([REQUEST_TIMEOUT, SLOW_TIMEOUT]).toEqual([10_000, 60_000]));

  it("gives a diff the slow limit", async () => {
    const f = vi.fn((_url: string, init?: RequestInit) =>
      new Promise<Response>((_, reject) => init!.signal!.addEventListener("abort", () => reject(init!.signal!.reason))));
    const api = new Api("http://h:1", undefined, f as any, { fast: 50, slow: 500 });
    const t0 = Date.now();
    await expect(api.getDiff("W-1")).rejects.toMatchObject({ status: 0, detail: "Kraft did not answer within 0.5s" });
    expect(Date.now() - t0).toBeGreaterThanOrEqual(450);
    await expect(api.getItem("W-1")).rejects.toMatchObject({ detail: "Kraft did not answer within 0.05s" });
  });
  it.each([
    ["a preview", (a: Api) => a.preview({ title: "T", description: "", repo: "/r", autostart: false })],
    ["a create", (a: Api) => a.create({ title: "T", description: "", repo: "/r", autostart: false })],
  ])("gives %s the slow limit: the server resolves the chain", async (_, call) => {
    const f = vi.fn((_url: string, init?: RequestInit) =>
      new Promise<Response>((_, reject) => init!.signal!.addEventListener("abort", () => reject(init!.signal!.reason))));
    await expect(call(new Api("http://h:1", undefined, f as any, { fast: 50, slow: 200 }))).rejects.toMatchObject({ detail: "Kraft did not answer within 0.2s" });
  });
});
