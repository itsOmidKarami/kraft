import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Takes the docs screenshots that come from the dashboard demo (`just docs-screenshots`): the built
 * demo on `vite preview`, as the demo smoke suite serves it, one test per picture. Not a test suite:
 * it writes PNGs into .github/assets/ and checks nothing beyond each page having drawn.
 *   cd frontend && npx playwright test -c e2e/docs-shots/playwright.config.ts [-g storage]
 *
 * DEMO_PORT moves the server (default 4329, beside the smoke suite's 4328), DEMO_DIST serves a
 * dist-demo already built (relative to frontend/), and SHOTS_OUT writes the pictures somewhere else.
 */
const PORT = Number(process.env.DEMO_PORT ?? 4329);
const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const DIST = process.env.DEMO_DIST && path.resolve(FRONTEND, process.env.DEMO_DIST);
const preview = `npx vite preview --mode demo --host 127.0.0.1 --port ${PORT} --strictPort`;

export default defineConfig({
  testDir: ".",
  testMatch: /shots\.ts$/,
  timeout: 60_000,
  workers: 1,
  reporter: [["list"]],
  outputDir: "../../test-results/docs-shots",
  use: {
    baseURL: `http://127.0.0.1:${PORT}/demo/`,
    actionTimeout: 8_000,
    // Dark, at 1x. The demo answers Kraft's default look (Ink, a violet accent, full colour).
    colorScheme: "dark",
    deviceScaleFactor: 1,
    locale: "en-US",
    timezoneId: "Europe/Berlin",
    viewport: { width: 1440, height: 700 },
  },
  webServer: {
    command: DIST ? `${preview} --outDir ${JSON.stringify(DIST)}` : `npm run build:demo && ${preview}`,
    url: `http://127.0.0.1:${PORT}/demo/`,
    reuseExistingServer: false,
    timeout: 180_000,
    cwd: FRONTEND,
  },
});
