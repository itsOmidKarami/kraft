import { afterEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import { OFFLINE_REFUSAL } from "../writeGuard";
import { request } from "./http";

afterEach(() => {
  useStore.setState({ connection: "connecting", loadError: null });
  vi.unstubAllGlobals();
});

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

  it.each([
    ["the live connection dropped", { connection: "reconnecting" as const, loadError: null }],
    ["the list read failed", { connection: "open" as const, loadError: "down" }],
  ])("answers a write with the offline words, sending nothing, when %s", async (_, state) => {
    useStore.setState(state);
    reply(200, {});
    expect(await request("/work-items/w1/retry", { method: "POST" })).toEqual({ status: 0, body: { detail: OFFLINE_REFUSAL } });
    expect(fetch).not.toHaveBeenCalled();
    expect((await request("/drafts")).status).toBe(200);
  });
});
