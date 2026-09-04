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
