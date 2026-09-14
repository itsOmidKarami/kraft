/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig, type ProxyOptions } from "vite";

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

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "dist" },
  server: { port: 5173, proxy },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    exclude: ["e2e/**", "sweep/**", "node_modules/**"],
    poolOptions: { threads: { execArgv }, forks: { execArgv } },
  },
});
