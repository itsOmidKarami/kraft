import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import base from "../playwright.sweep.config";

/**
 * The UI contract suite: the built SPA on `vite preview`, `/api/**` mocked from sweep/fixtures.ts, no server
 * of Kraft's own. `just ui-contract` runs it; sweep/README.md says what it holds.
 *   cd frontend && npx playwright test -c sweep/contract/playwright.config.ts
 * Its own port (CONTRACT_PORT), so it can run beside the screenshot sweep; `--strictPort` fails loudly when
 * the port is taken, since a leftover server would serve a stale build to every row.
 */
const PORT = Number(process.env.CONTRACT_PORT ?? 4327);
const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const server = Array.isArray(base.webServer) ? base.webServer[0] : base.webServer!;

export default defineConfig({
  ...base,
  testDir: ".",
  testMatch: /\.spec\.ts$/,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["github"]] : [["list"]],
  use: { ...base.use, baseURL: `http://127.0.0.1:${PORT}` },
  webServer: {
    ...server,
    command: `npm run build && npx vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}`,
    cwd: FRONTEND,
  },
});
