import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * The dashboard demo's smoke suite: the built demo (`npm run build:demo`) on `vite preview`, nothing
 * mocked from outside, since the demo answers its own /api. One spec: it boots, and every things-to-try
 * Show me finds its target, so a UI change that strands the list fails CI. `just demo-smoke` runs it.
 *   cd frontend && npx playwright test -c e2e/demo/playwright.config.ts
 *
 * As the UI contract's config does: never reuses a server, its own port (DEMO_PORT, default 4328) with
 * `--strictPort`, `--host 127.0.0.1`, and DEMO_DIST to serve a dist-demo already built (relative to frontend/).
 */
const PORT = Number(process.env.DEMO_PORT ?? 4328);
const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
// Resolved here: the demo build's root is demo/, so vite would read a relative --outDir from there.
const DIST = process.env.DEMO_DIST && path.resolve(FRONTEND, process.env.DEMO_DIST);
const BASE = process.env.KRAFT_DEMO_BASE ?? "/demo/";
const preview = `npx vite preview --mode demo --host 127.0.0.1 --port ${PORT} --strictPort`;

export default defineConfig({
  testDir: ".",
  testMatch: /\.spec\.ts$/,
  timeout: 60_000,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["github"]] : [["list"]],
  outputDir: "../../test-results/demo",
  use: {
    baseURL: `http://127.0.0.1:${PORT}${BASE}`,
    actionTimeout: 8_000,
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "Europe/Berlin",
  },
  webServer: {
    command: DIST ? `${preview} --outDir ${JSON.stringify(DIST)}` : `npm run build:demo && ${preview}`,
    url: `http://127.0.0.1:${PORT}${BASE}`,
    reuseExistingServer: false,
    timeout: 180_000,
    cwd: FRONTEND,
  },
});
