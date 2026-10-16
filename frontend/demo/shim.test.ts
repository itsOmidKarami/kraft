import { afterEach, describe, expect, it, vi } from "vitest";
import { ABORT, HANG, type MockServer, type Reply } from "../e2e/contract/mocks/mockApi";
import { DemoSocket, patchFetch, patchWebSocket, restoreAddress, shiftClock } from "./shim";

/** A server that answers every request with `reply` and records what it was asked. */
const recording = (reply: Reply) => {
  const seen: { method: string; path: string; body: unknown }[] = [];
  const serve: MockServer = async (req) => {
    seen.push({ method: req.method, path: new URL(req.url).pathname, body: req.json() });
    return reply;
  };
  return { serve, seen };
};

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("patchFetch", () => {
  it.each([
    ["a JSON answer", { status: 200, contentType: "application/json", body: '{"ok":true}' }, 200, "application/json", '{"ok":true}'],
    ["a 204 with no body", { status: 204 }, 204, null, ""],
    ["a refusal with its detail", { status: 409, contentType: "application/json", body: '{"detail":"gate is not pending"}' }, 409, "application/json", '{"detail":"gate is not pending"}'],
  ])("answers /api/** from the mock: %s", async (_, reply, status, type, text) => {
    vi.stubGlobal("fetch", vi.fn());
    const { serve } = recording(reply);
    patchFetch(serve);
    const res = await fetch("/api/work-items");
    expect([res.status, res.headers.get("content-type"), await res.text()]).toEqual([status, type, text]);
  });

  it("hands the mock the method, the path and the parsed body", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const { serve, seen } = recording({ status: 200 });
    patchFetch(serve);
    await fetch("/api/work-items/abc/gates/spec_approval/reject", { method: "POST", body: JSON.stringify({ note: "too vague" }) });
    expect(seen).toEqual([{ method: "POST", path: "/api/work-items/abc/gates/spec_approval/reject", body: { note: "too vague" } }]);
  });

  it("rejects as a refused connection does when the mock aborts", async () => {
    vi.stubGlobal("fetch", vi.fn());
    patchFetch(recording(ABORT).serve);
    await expect(fetch("/api/health")).rejects.toBeInstanceOf(TypeError);
  });

  it("never settles when the mock hangs, until the request is aborted", async () => {
    vi.stubGlobal("fetch", vi.fn());
    patchFetch(recording(HANG).serve);
    const ctl = new AbortController();
    const pending = fetch("/api/work-items", { signal: ctl.signal });
    const raced = await Promise.race([pending.then(() => "settled"), new Promise((r) => setTimeout(() => r("pending"), 20))]);
    ctl.abort();
    expect(raced).toBe("pending");
    await expect(pending).rejects.toBeDefined();
  });

  it.each([
    ["another origin", "https://example.com/api/health"],
    ["a path outside /api/", "/demo/assets/app.js"],
  ])("leaves %s to the network", async (_, url) => {
    const real = vi.fn(async () => new Response("net"));
    vi.stubGlobal("fetch", real);
    const { serve, seen } = recording({ status: 200 });
    patchFetch(serve);
    expect(await (await fetch(url)).text()).toBe("net");
    expect(seen).toEqual([]);
  });
});

describe("patchWebSocket", () => {
  it("opens a silent socket for the events route, and closes it when asked", async () => {
    const Real = vi.fn();
    vi.stubGlobal("WebSocket", Real);
    patchWebSocket();
    const ws = new WebSocket(`${location.origin.replace(/^http/, "ws")}/api/ws/events?after_seq=0`);
    expect(ws).toBeInstanceOf(DemoSocket);
    const opened = new Promise((r) => (ws.onopen = r));
    await opened;
    expect(ws.readyState).toBe(DemoSocket.OPEN);
    const closed = new Promise((r) => (ws.onclose = r));
    ws.close();
    await closed;
    expect([ws.readyState, Real.mock.calls.length]).toEqual([DemoSocket.CLOSED, 0]);
  });

  it("leaves any other socket (Vite's reload channel) to the real WebSocket", () => {
    const Real = vi.fn();
    vi.stubGlobal("WebSocket", Real);
    patchWebSocket();
    new WebSocket("ws://localhost:5174/?token=x");
    expect(Real).toHaveBeenCalledTimes(1);
  });
});

describe("shiftClock", () => {
  it("runs Date from the scenario's moment, and leaves an explicit date alone", () => {
    vi.stubGlobal("Date", Date);
    const to = Date.parse("2026-09-13T10:10:00.000Z");
    shiftClock(to);
    expect(Math.abs(Date.now() - to)).toBeLessThan(1000);
    expect(Math.abs(new Date().getTime() - to)).toBeLessThan(1000);
    expect(new Date("2026-01-02T03:04:05.000Z").toISOString()).toBe("2026-01-02T03:04:05.000Z");
    expect(new Date(2026, 0, 2).getFullYear()).toBe(2026);
  });
});

describe("restoreAddress", () => {
  it.each([
    ["an address the 404 page sent back", "/demo/?to=%2Fwork-items%2Fabc%3Fsel%3Dspec", "/demo/work-items/abc?sel=spec"],
    ["no ?to= at all", "/demo/settings/policy", "/demo/settings/policy"],
    ["a protocol-relative ?to=, which is not a path", "/demo/?to=%2F%2Fexample.com%2Fx", "/demo/?to=%2F%2Fexample.com%2Fx"],
  ])("%s", (_, from, to) => {
    window.history.replaceState(null, "", from);
    restoreAddress("/demo/");
    expect(location.pathname + location.search).toBe(to);
  });
});
