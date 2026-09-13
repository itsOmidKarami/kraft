import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

// A request that misses /api/ is not a 404 under `just dev`, it is vite's SPA
// fallback answering 200 with index.html, so `res.json()` dies with a
// SyntaxError about `<` and the screen looks broken for an unrelated reason.
// Every backend route lives under /api/ (server side: src/kraft/api.py's
// api_router). `req()` is where most calls end up, but `logStreamUrl` and
// `getLogText` build a request outside it (EventSource, a plain-text fetch)
// and are the ones most likely to drift back to a bare path — this checks
// all three, plus the one call site in ws.ts, still go through /api.
describe("the dev vite proxy", () => {
  it("every fetch/WebSocket call site in api.ts and ws.ts uses /api", () => {
    const apiTs = readFileSync(join(here, "api.ts"), "utf-8");
    const wsTs = readFileSync(join(here, "ws.ts"), "utf-8");
    const viteConfig = readFileSync(join(here, "..", "vite.config.ts"), "utf-8");

    expect(apiTs).toMatch(/fetch\(apiUrl\(path\)/); // req()
    expect([...apiTs.matchAll(/apiUrl\(logUrl\(sessionId\)\)/g)]).toHaveLength(2); // logStreamUrl, getLogText
    expect(wsTs).toMatch(/\/api\/ws\/events/);
    expect(viteConfig).toMatch(/["']\/api["']/);
  });

  it("the proxy target reads KRAFT_PORT so it cannot drift from the dev backend's own port", () => {
    const viteConfig = readFileSync(join(here, "..", "vite.config.ts"), "utf-8");
    expect(viteConfig).toMatch(/process\.env\.KRAFT_PORT/);
  });

  // Kraft-y0g2: the justfile owns the dev port (`dev_port`, default 8766) and
  // exports it as KRAFT_PORT for `just dev` and `just ui`. This fallback is what
  // a bare `npm run dev` gets, and it used to say 8765 -- an installed daemon's
  // default -- so dev clicks landed in the operator's real instance. The two
  // defaults have to stay equal; nothing but this test says so.
  it("the proxy fallback port matches the justfile's dev_port default", () => {
    const viteConfig = readFileSync(join(here, "..", "vite.config.ts"), "utf-8");
    const justfile = readFileSync(join(here, "..", "..", "justfile"), "utf-8");

    const fallback = viteConfig.match(/process\.env\.KRAFT_PORT \?\? "(\d+)"/)?.[1];
    const devPort = justfile.match(/dev_port := env_var_or_default\("KRAFT_DEV_PORT", "(\d+)"\)/)?.[1];

    expect(fallback).toBeDefined();
    expect(devPort).toBeDefined();
    expect(fallback).toBe(devPort);
  });
});
