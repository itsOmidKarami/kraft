import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  use: { baseURL: process.env.KRAFT_E2E_BASE ?? "http://127.0.0.1:8765" },
  reporter: "list",
});
