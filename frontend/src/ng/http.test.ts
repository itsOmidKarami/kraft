import { afterEach, describe, expect, it, vi } from "vitest";
import { request } from "./http";

afterEach(() => vi.unstubAllGlobals());

const reply = (status: number, body: unknown) => vi.stubGlobal("fetch", vi.fn(async () => new Response(body === undefined ? null : JSON.stringify(body), { status })));

describe("ng request", () => {
  it("hands back a refusal's body whole, with its status", async () => {
    const body = { detail: "published since this draft began", files: { "chains/default.yaml": { diff: "-a\n+b\n" } } };
    reply(409, body);
    expect(await request("/drafts/chains/default/publish", { method: "POST" })).toEqual({ status: 409, body });
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/drafts/chains/default/publish");
  });

  it("dispatches kraft:unauthenticated on a 401, as api.ts does", async () => {
    const seen = vi.fn();
    window.addEventListener("kraft:unauthenticated", seen);
    reply(401, { detail: "sign in" });
    expect((await request("/drafts")).status).toBe(401);
    reply(422, { detail: "no" });
    await request("/drafts");
    window.removeEventListener("kraft:unauthenticated", seen);
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("answers 204 with no body, and names the server when it can't be reached", async () => {
    reply(204, undefined);
    expect(await request("/drafts/chains/x", { method: "DELETE" })).toEqual({ status: 204, body: undefined });
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const a = await request("/drafts");
    expect(a.status).toBe(0);
    expect((a.body as { detail: string }).detail).toMatch(/could not reach the Kraft server/);
  });
});
