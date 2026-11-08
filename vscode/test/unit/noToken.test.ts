import { expect, it, vi } from "vitest";
import { Api } from "../../src/core/api";
import { connectionFor } from "../../src/core/connection";
import { Store } from "../../src/core/store";

// The anti-goal: the local mcp-token reaching a signed-in URL, on any path.
it("with a stored session, neither a request, the log stream nor the websocket carries the token", async () => {
  const c = connectionFor({ setting: "https://k.example", globalSetting: "https://k.example", env: {}, accessYaml: undefined, token: "SECRET-TOKEN", session: () => "SESS" });
  expect(c.token).toBeUndefined();
  const f = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ items: [], cursor: 0 }), { status: 200 }));
  const api = new Api(c.base, c.session ? { session: c.session } : c.token, f as any);
  await api.listItems();
  const log = vi.fn(async (_url: string, _init?: RequestInit) => new Response("event: end\ndata: {}\n\n", { status: 200 }));
  const streamApi = new Api(c.base, { session: "SESS" }, log as any);
  for await (const _ of streamApi.streamLog("W", "s", new AbortController().signal)) { /* none */ }
  const seenHeaders: Record<string, string>[] = [];
  const store = new Store(api, (_url, headers) => {
    seenHeaders.push(headers);
    return { onOpen() {}, onMessage() {}, onClose() {}, close() {} };
  });
  await store.start();
  const all = JSON.stringify([f.mock.calls, log.mock.calls, seenHeaders]);
  expect(all).not.toContain("SECRET-TOKEN");
  expect(all).not.toContain("Bearer");
  expect(seenHeaders[0]).toEqual({ Cookie: "kraft_session=SESS" });
  expect(api.wsUrl(1)).toMatch(/^wss:\/\//);
  store.stop();
});
