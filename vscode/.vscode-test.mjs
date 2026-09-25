import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "@vscode/test-cli";

export default defineConfig({
  // In order: the review rejects a gate, the next test approves the one that comes back.
  files: ["1-review", "2-gates", "3-config"].map((n) => `out/vscode/test/integration/${n}.test.js`),
  workspaceFolder: process.env.KRAFT_TEST_WORKSPACE,
  mocha: { timeout: 180_000 },
  // A short user-data dir: VS Code's IPC socket path must fit in ~100 chars.
  launchArgs: ["--disable-extensions", "--user-data-dir", mkdtempSync(join(tmpdir(), "kvsc-"))],
});
