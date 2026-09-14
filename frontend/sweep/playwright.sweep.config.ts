import { defineConfig } from "@playwright/test";

/**
 * UI sweep config. No orchestrator: the spec mocks /api/** itself, so the
 * only server is `vite preview` over a fresh build (history fallback → SPA).
 *   cd frontend && npx playwright test -c sweep/playwright.sweep.config.ts
 *
 * Never reuses a server: a leftover `vite preview` on the port (from another
 * checkout or worktree) silently served a stale build to every shot. The
 * port is its own (SWEEP_PORT, default 4317) and `--strictPort` fails loudly
 * if it is taken. SWEEP_DIST serves an already-built dist instead of
 * building this checkout (used to shoot a baseline from a clean worktree).
 */
const PORT = Number(process.env.SWEEP_PORT ?? 4317);
const DIST = process.env.SWEEP_DIST;

export default defineConfig({
  testDir: ".",
  testMatch: /(sweep|elements|interactions|checks)\.spec\.ts$/,
  timeout: 60_000,
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "../e2e-shots/sweep/playwright-report.json" }]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    // A step waiting forever on a missing element hit the 60s test timeout
    // and recorded nothing; a bounded action records `step.error` and the
    // flow carries on to its next step.
    actionTimeout: 8_000,
    colorScheme: "dark",
    deviceScaleFactor: 1,
    locale: "en-US",
    timezoneId: "Europe/Berlin",
  },
  webServer: {
    command: DIST
      ? `npx vite preview --port ${PORT} --strictPort --outDir ${JSON.stringify(DIST)}`
      : `npm run build && npx vite preview --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false,
    timeout: 180_000,
    cwd: "..",
  },
});
