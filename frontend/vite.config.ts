/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig, type ProxyOptions } from "vite";

const API = "http://127.0.0.1:8765";
const proxy: Record<string, ProxyOptions> = Object.fromEntries(
  [
    "/work-items",
    "/templates",
    "/health",
    "/worker-sessions",
    "/search",
    "/documents",
    "/index",
    "/analytics",
    "/repos",
    "/registry",
    "/policy",
    "/access",
    "/sessions",
    "/login",
    "/logout",
    "/beads",
    // Four Settings screens were dev-only broken without these. `/theme` was
    // traced while writing the spec and is not in the bead. `src/vite.proxy.test.ts`
    // fails when the next route is added without a line here.
    "/steering",
    "/intake",
    "/notify",
    "/theme",
  ].map((p) => [
    p,
    { target: API, changeOrigin: true },
  ]),
);
proxy["/ws"] = { target: API, ws: true, changeOrigin: true };

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
  },
});
