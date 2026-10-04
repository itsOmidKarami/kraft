import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * The UI contract suite: the built SPA on `vite preview`, `/api/**` mocked from mocks/, no server of Kraft's
 * own. `just ui-contract` runs it; README.md says what it holds.
 *   cd frontend && npx playwright test -c e2e/contract/playwright.config.ts
 *
 * Never reuses a server: a leftover `vite preview` on the port (from another checkout or worktree) would
 * serve a stale build to every row. The port is its own (CONTRACT_PORT, default 4327) and `--strictPort`
 * fails loudly if it is taken. CONTRACT_DIST serves an already-built dist (a path relative to frontend/)
 * instead of building this checkout. `--host 127.0.0.1`: without it `vite preview` can bind `::1` only
 * (where `localhost` resolves to IPv6 first) and the wait on 127.0.0.1 times out.
 */
const PORT = Number(process.env.CONTRACT_PORT ?? 4327);
const DIST = process.env.CONTRACT_DIST;
const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const preview = `npx vite preview --host 127.0.0.1 --port ${PORT} --strictPort`;

export default defineConfig({
  testDir: ".",
  testMatch: /\.spec\.ts$/,
  timeout: 60_000,
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["github"]] : [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    // A bounded action fails its row with its own error, instead of waiting out the 60s test timeout on a missing element.
    actionTimeout: 8_000,
    colorScheme: "dark",
    deviceScaleFactor: 1,
    locale: "en-US",
    timezoneId: "Europe/Berlin",
  },
  webServer: {
    command: DIST ? `${preview} --outDir ${JSON.stringify(DIST)}` : `npm run build && ${preview}`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false,
    timeout: 180_000,
    cwd: FRONTEND,
  },
});
