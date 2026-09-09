import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

/** Every `/first-segment` a string literal in `api.ts` starts with. Both plain
 *  strings ("/health") and template literals (`/work-items/${id}/diff`) begin
 *  with a quote character immediately followed by the path, so one regex finds
 *  both, and the segment stops before the first `/` or `?` that follows. */
const prefixes = (source: string) =>
  new Set([...source.matchAll(/["'`](\/[a-zA-Z0-9_-]+)/g)].map((m) => m[1]));

// The dev proxy list has drifted at least twice: a prefix missing from
// vite.config.ts is not a 404 under `just dev`, it is vite's SPA fallback
// answering 200 with index.html, so `api.req()` dies inside `res.json()` with a
// SyntaxError about `<` and the screen looks broken for an unrelated reason.
// A subset assertion is cheaper than a route registry and fails on the next
// route added without a proxy entry.
describe("the dev vite proxy", () => {
  it("forwards every route prefix api.ts requests", () => {
    const requested = prefixes(readFileSync(join(here, "api.ts"), "utf-8"));
    const proxied = prefixes(readFileSync(join(here, "..", "vite.config.ts"), "utf-8"));

    expect([...requested].filter((p) => !proxied.has(p))).toEqual([]);
  });
});
