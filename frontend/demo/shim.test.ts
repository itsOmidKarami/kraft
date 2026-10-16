import { afterEach, describe, expect, it, vi } from "vitest";
import { buildScenario } from "../e2e/contract/mocks/fixtures";
import { ABORT, HANG, mockServer, type MockServer, type Reply } from "../e2e/contract/mocks/mockApi";
import { useStore } from "../src/store";
import { connectEvents } from "../src/ws";
import { broadcast, DemoSocket, patchFetch, patchWebSocket, restoreAddress, serveApiLinks, shiftClock } from "./shim";

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

  it("hands the network a Request it has not read, body and all", async () => {
    const real = vi.fn(async (input: Request) => new Response(await input.text()));
    vi.stubGlobal("fetch", real);
    patchFetch(recording({ status: 200 }).serve);
    const res = await fetch(new Request("https://example.com/upload", { method: "POST", body: "payload" }));
    expect(await res.text()).toBe("payload");
  });

  it("rejects at once when the request was aborted before the mock hung", async () => {
    vi.stubGlobal("fetch", vi.fn());
    patchFetch(recording(HANG).serve);
    const ctl = new AbortController();
    ctl.abort();
    await expect(fetch("/api/work-items", { signal: ctl.signal })).rejects.toBeDefined();
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

describe("serveApiLinks", () => {
  it.each([
    ["a link to a new tab", { target: "_blank" }, {}],
    ["a ctrl-click on a plain link", {}, { ctrlKey: true }],
  ])("opens %s in the click itself, while it is still the person's, then shows the answer there", async (_, attrs, keys) => {
    vi.stubGlobal("fetch", vi.fn());
    patchFetch(recording({ status: 200, contentType: "text/plain", body: "a log" }).serve);
    const tab = { opener: window as unknown, location: { href: "" } };
    const open = vi.fn(() => tab);
    vi.stubGlobal("open", open);
    const blob = vi.fn(() => "blob:the-log");
    vi.stubGlobal("URL", Object.assign(class extends URL {}, { createObjectURL: blob, revokeObjectURL: vi.fn() }));
    const doc = document.implementation.createHTMLDocument();
    serveApiLinks(doc);
    const a = Object.assign(doc.createElement("a"), { href: `${location.origin}/api/work-items/w1/sessions/s1/log`, ...attrs });
    doc.body.append(a);
    a.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, ...keys }));
    expect(open).toHaveBeenCalledWith("", "_blank");
    await vi.waitFor(() => expect(tab.location.href).toBe("blob:the-log"));
    expect(tab.opener).toBeNull();
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

describe("broadcast", () => {
  it.each([
    ["an open socket gets the event", "open", ['{"seq":4243,"type":"pause_requested"}']],
    ["a socket still connecting when the frame is sent gets nothing", "connecting", []],
    ["a closed socket gets nothing", "closed", []],
    ["a socket closing gets nothing", "closing", []],
  ])("%s", async (_, state, want) => {
    const send = () => broadcast({ seq: 4243, type: "pause_requested" });
    // Connecting: made after the send, so it opens before the frame is delivered, and still was not open when it was sent.
    if (state === "connecting") send();
    const ws = new DemoSocket("ws://localhost/api/ws/events");
    const got: string[] = [];
    ws.onmessage = (e) => got.push(e.data);
    if (state !== "connecting") await new Promise((r) => (ws.onopen = r));
    if (state === "closed") {
      const closed = new Promise((r) => (ws.onclose = r));
      ws.close();
      await closed;
    }
    if (state === "closing") ws.close();
    if (state !== "connecting") send();
    // A frame arrives on a later task, never inside the call that sent it.
    expect(got).toEqual([]);
    await new Promise((r) => setTimeout(r, 0));
    expect(got).toEqual(want);
    ws.close();
  });

  it("delivers one action's frames in one task, before anything the first sets off", async () => {
    const ws = new DemoSocket("ws://localhost/api/ws/events");
    await new Promise((r) => (ws.onopen = r));
    const order: string[] = [];
    ws.onmessage = (e) => { const { type } = JSON.parse(e.data); order.push(type); queueMicrotask(() => order.push(`after ${type}`)); };
    broadcast({ seq: 4243, type: "pause_requested" });
    broadcast({ seq: 4244, type: "worker_session_paused" });
    await new Promise((r) => setTimeout(r, 0));
    expect(order).toEqual(["pause_requested", "worker_session_paused", "after pause_requested", "after worker_session_paused"]);
    ws.close();
  });

  it("answers the action before any frame, and a listener that throws harms neither", async () => {
    const S = buildScenario("default");
    const serve = mockServer(S, { ngBoard: true, onEvent: broadcast });
    const id = S.ngBoard.find((x) => S.bundles[x].item.bead_id === "kraft-91bc")!;
    const ws = new DemoSocket("ws://localhost/api/ws/events");
    await new Promise((r) => (ws.onopen = r));
    const order: string[] = [];
    ws.onmessage = (e) => { order.push(JSON.parse(e.data).type); throw new Error("a listener broke"); };
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    const r = (await serve({ method: "POST", url: `http://localhost/api/work-items/${id}/pause`, json: () => ({}) })) as { status: number };
    order.push(`answer ${r.status}`);
    await new Promise((res) => setTimeout(res, 0));
    expect(order).toEqual(["answer 200", "pause_requested", "worker_session_paused"]);
    expect([S.bundles[id].item.status, S.bundles[id].events.slice(-2).map((e) => e.type)]).toEqual(["paused", ["pause_requested", "worker_session_paused"]]);
    expect(logged).toHaveBeenCalledTimes(2);
    logged.mockRestore();
    ws.close();
  });

  it("moves the board's row when an action's event is forwarded", async () => {
    vi.stubGlobal("fetch", vi.fn());
    vi.stubGlobal("WebSocket", vi.fn());
    const S = buildScenario("default");
    patchFetch(mockServer(S, { ngBoard: true, onEvent: broadcast }));
    patchWebSocket();
    await useStore.getState().bootstrap();
    const id = S.ngBoard.find((x) => S.bundles[x].item.bead_id === "kraft-91bc")!;
    const shown = () => useStore.getState().workItems[id].display_status;
    expect(shown()).toBe("running");
    const stop = connectEvents();
    await vi.waitFor(() => expect(useStore.getState().connection).toBe("open"));
    // The page's own write, as the peek's Pause makes it: nothing re-reads the list after it.
    await fetch(`/api/work-items/${id}/pause`, { method: "POST", body: "{}" });
    await vi.waitFor(() => expect(shown()).toBe("paused"));
    // Every re-read the frames set off has landed: each event is held once, not once per read and frame.
    await new Promise((r) => setTimeout(r, 50));
    const seqs = useStore.getState().eventsByItem[id].map((e) => e.seq);
    expect(seqs.length).toBe(new Set(seqs).size);
    expect(useStore.getState().eventsByItem[id].slice(-2).map((e) => e.type)).toEqual(["pause_requested", "worker_session_paused"]);
    stop();
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
