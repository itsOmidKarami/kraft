import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  // One orchestrator serves every spec, and chain.spec mutates the board that
  // search.spec reads. Shared mutable state -> run them one at a time.
  workers: 1,
  use: { baseURL: process.env.KRAFT_E2E_BASE ?? "http://127.0.0.1:8765" },
  reporter: "list",
});
