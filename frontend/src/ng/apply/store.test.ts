// @vitest-environment jsdom
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../store";
import { GIVE_UP_MS, onApplyFrame, POLL_MS, useApply, watchApply } from "./store";

const reply = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const PENDING = { restart: [{ id: "access.port", file: "access.yaml", text: "port changes from 8765 to 9000" }], reload: [], managed: true };

let calls: { path: string; method: string }[];
let routes: Record<string, () => Response | Promise<Response>>;
beforeEach(() => {
  calls = [];
  routes = {};
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const path = String(url).replace(/^.*\/api/, "");
    calls.push({ path, method: init?.method ?? "GET" });
    const h = routes[`${init?.method ?? "GET"} ${path}`];
    if (!h) return Promise.reject(new TypeError("no route"));
    return Promise.resolve(h());
  });
  useApply.setState({ restart: [], reload: [], managed: false, loaded: false, phase: "idle", confirming: false, address: "", error: null });
  useStore.setState({ connection: "connecting" } as never);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const posts = () => calls.filter((c) => c.path === "/apply/restart");

describe("apply store", () => {
  it("reads /apply, with managed only when the server says true", async () => {
    routes["GET /apply"] = () => reply(200, PENDING);
    await useApply.getState().refresh();
    expect(useApply.getState()).toMatchObject({ restart: PENDING.restart, managed: true, loaded: true });
    routes["GET /apply"] = () => reply(200, { restart: [], reload: [] });
    await useApply.getState().refresh();
    expect(useApply.getState().managed).toBe(false);
  });

  it("refetches on an apply_changed frame, not on another", async () => {
    routes["GET /apply"] = () => reply(200, PENDING);
    onApplyFrame({ type: "intake_checked" });
    expect(calls).toHaveLength(0);
    onApplyFrame({ type: "apply_changed" });
    expect(calls.map((c) => c.path)).toEqual(["/apply"]);
  });

  it("refetches on a reconnect and on window focus, since the frame is never replayed", async () => {
    routes["GET /apply"] = () => reply(200, PENDING);
    const stop = watchApply();
    expect(calls).toHaveLength(1);
    useStore.setState({ connection: "open" } as never);
    expect(calls).toHaveLength(2);
    useStore.setState({ connection: "reconnecting" } as never);
    expect(calls).toHaveLength(2);
    useStore.setState({ connection: "open" } as never);
    expect(calls).toHaveLength(3);
    window.dispatchEvent(new Event("focus"));
    expect(calls).toHaveLength(4);
    stop();
    window.dispatchEvent(new Event("focus"));
    expect(calls).toHaveLength(4);
  });

  it("asks to restart toward the port Kraft will run on: the saved one when it changes, the environment's when that wins", async () => {
    useApply.setState({ managed: true, restart: [{ id: "access.bind", file: "access.yaml", text: "bind changes from 127.0.0.1 to 0.0.0.0" }] });
    routes["GET /access"] = () => reply(200, { bind: "0.0.0.0", port: 8765 });
    routes["GET /health"] = () => reply(200, { bind: "127.0.0.1", port: 8771 });
    await useApply.getState().askRestart();
    expect(useApply.getState().address).toBe(`${location.protocol}//${location.hostname}:8771`);
    useApply.setState({ restart: [{ id: "access.port", file: "access.yaml", text: "port changes from 8771 to 8765" }] });
    await useApply.getState().askRestart();
    expect(useApply.getState().address).toBe(`${location.protocol}//${location.hostname}:8765`);
  });

  it("never posts a restart for a server that is not managed", async () => {
    await useApply.getState().askRestart();
    await useApply.getState().runRestart();
    expect(useApply.getState().confirming).toBe(false);
    expect(posts()).toHaveLength(0);
  });

  it("turns a 409 into unmanaged and says to restart it yourself", async () => {
    useApply.setState({ managed: true, address: location.origin });
    routes["POST /apply/restart"] = () => reply(409, { detail: "started from a terminal: restart it there" });
    await useApply.getState().runRestart();
    expect(useApply.getState()).toMatchObject({ managed: false, phase: "idle" });
    expect(useApply.getState().error).toMatch(/cannot restart itself\. Run kraft admin restart; .*Ctrl-C/);
  });

  it("waits for the server to go down and come back, then refetches", async () => {
    vi.useFakeTimers();
    useApply.setState({ managed: true, address: location.origin });
    routes["POST /apply/restart"] = () => reply(202, { restarting: true });
    routes["GET /apply"] = () => reply(200, { restart: [], reload: [], managed: true });
    let health = 0;
    routes["GET /health"] = () => {
      health += 1;
      if (health === 1) return reply(200, {}); // still the old process
      if (health <= 3) throw new TypeError("down");
      return reply(200, {});
    };
    const done = useApply.getState().runRestart();
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(useApply.getState().phase).toBe("restarting");
    await vi.advanceTimersByTimeAsync(POLL_MS * 4);
    await done;
    expect(useApply.getState().phase).toBe("idle");
    expect(calls.some((c) => c.path === "/apply")).toBe(true);
  });

  it("says it did not come back, never done, when nothing answers in a minute", async () => {
    vi.useFakeTimers();
    useApply.setState({ managed: true, address: location.origin });
    routes["POST /apply/restart"] = () => reply(202, { restarting: true });
    const done = useApply.getState().runRestart();
    await vi.advanceTimersByTimeAsync(GIVE_UP_MS + POLL_MS * 2);
    await done;
    expect(useApply.getState().phase).toBe("stuck");
    expect(useApply.getState().error).toMatch(/did not come back/);
  });
});

describe("the restart call", () => {
  it("has one call site in the UI, in the store", () => {
    const here = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
    const hits: string[] = [];
    const walk = (dir: string) => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        const p = join(dir, e.name);
        if (e.isDirectory()) walk(p);
        else if (/\.tsx?$/.test(e.name) && !e.name.includes(".test.") && readFileSync(p, "utf-8").includes("/apply/restart")) hits.push(p.slice(here.length + 1));
      }
    };
    walk(here);
    expect(hits).toEqual(["ng/apply/store.ts"]);
  });
});
