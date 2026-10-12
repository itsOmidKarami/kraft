import { describe, expect, it, vi } from "vitest";
import { Api, ApiError } from "../../src/core/api";

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
});
