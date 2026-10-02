import { createRequire } from "node:module";
import { dirname } from "node:path";
import react from "@vitejs/plugin-react";
import type { Plugin, ProxyOptions } from "vite";
import { defineConfig } from "vitest/config";
import { packageNotice, thirdPartyLicenses } from "../dev/third_party_licenses.mjs";

// Kraft-y0g2: the fallback is the *dev* instance's port (justfile's `dev_port`),
// not 8765. `just ui` and `just dev` both export KRAFT_PORT, so this only applies
// to a bare `npm run dev` — where 8765 meant proxying to whatever installed
// daemon happens to be running, writing dev clicks into the operator's real
// instance. Keep this in step with the justfile's `dev_port` default.
const API = `http://127.0.0.1:${process.env.KRAFT_PORT ?? "8766"}`;
// Every backend route lives under /api/ (including /api/ws/events), so one
// prefix covers all of them — no more per-route entries to keep in sync with
// api.ts. src/vite.proxy.test.ts still fails if api.ts starts requesting a
// literal outside /api/.
const proxy: Record<string, ProxyOptions> = {
  "/api": { target: API, changeOrigin: true, ws: true },
};

// Node 22+'s own global `localStorage` (a no-op stub unless the process is run
// with --localstorage-file) shadows jsdom's window.localStorage in the workers
// vitest spawns, leaving it undefined. Disable Node's copy so jsdom's is the
// only one in scope. Node 20 (CI's node:20 image) has no such global and
// rejects the flag with "bad option", killing every worker, so only pass it
// where it exists.
const execArgv = process.allowedNodeEnvironmentFlags.has("--experimental-webstorage")
  ? ["--no-experimental-webstorage"]
  : [];

// The bundle carries react, lucide-react, the Inter font and the rest, minified
// past their license comments. Their notices ship beside it instead, in
// THIRD_PARTY_LICENSES.txt, which `just bundle` copies into the wheel with the
// rest of dist/. Every module in the graph counts, so a package that is only a
// re-export (react-router-dom) is listed with the one it re-exports. Vite's own
// runtime helpers (the modulepreload polyfill, the preload helper) are virtual
// modules, `\0vite/...`, which name no package, so vite is added by hand.
const licenses: Plugin = {
  name: "kraft:third-party-licenses",
  apply: "build",
  generateBundle() {
    const ids = [...this.getModuleIds()];
    const vite = ids.some((id) => id.startsWith("\0vite/"))
      ? [
          packageNotice(dirname(createRequire(import.meta.url).resolve("vite/package.json")), {
            before: "# Licenses of bundled dependencies",
          }),
        ]
      : [];
    this.emitFile({
      type: "asset",
      fileName: "THIRD_PARTY_LICENSES.txt",
      source: thirdPartyLicenses(ids, "Kraft's web UI", vite),
    });
  },
};

export default defineConfig({
  plugins: [react(), licenses],
  base: "/",
  build: { outDir: "dist" },
  server: { port: 5173, proxy },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    unstubGlobals: true,
    exclude: ["e2e/**", "sweep/**", "node_modules/**"],
    execArgv,
  },
});
