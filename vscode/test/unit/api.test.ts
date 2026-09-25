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
    expect(url).toBe("http://h:1/api/work-items");
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
});
