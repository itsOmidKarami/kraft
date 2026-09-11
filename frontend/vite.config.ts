/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig, type ProxyOptions } from "vite";

const API = "http://127.0.0.1:8765";
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
    exclude: ["e2e/**", "node_modules/**"],
    poolOptions: { threads: { execArgv }, forks: { execArgv } },
  },
});
