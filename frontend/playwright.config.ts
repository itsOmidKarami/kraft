import { defineConfig } from "@playwright/test";
import { scaledTimeout } from "./e2e-timing";

// serve.py (frontend/e2e/serve.py) prints this once its fixture orchestrator
// is healthy. No fallback to 127.0.0.1:8765: that address is the installed
// daemon's, and defaulting to it sent a whole suite at a human's real
// ~/.kraft when the fixture server failed to come up (Kraft-m1e8).
const baseURL = process.env.KRAFT_E2E_BASE;
if (!baseURL) {
  throw new Error(
    "KRAFT_E2E_BASE is not set. Start the fixture server first: " +
      "`uv run python frontend/e2e/serve.py`, then copy its KRAFT_E2E_BASE line."
  );
}

export default defineConfig({
  testDir: "./e2e",
  timeout: scaledTimeout(120_000),
  // Playwright's fixed 5s default for bare `expect(...)` assertions (no
  // explicit timeout) doesn't scale with KRAFT_E2E_TIMEOUT_SCALE otherwise --
  // search.spec has zero explicit timeouts, so under load it degrades exactly
  // like the unscaled suite did.
  expect: { timeout: scaledTimeout(5_000) },
  // One orchestrator serves every spec, and chain.spec mutates the board that
  // search.spec reads. Shared mutable state -> run them one at a time.
  workers: 1,
  use: { baseURL },
  reporter: "list",
});
