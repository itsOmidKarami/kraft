import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "@vscode/test-cli";

const files = (names) => names.map((n) => `out/vscode/test/integration/${n}.test.js`);
// A short user-data dir: VS Code's IPC socket path must fit in ~100 chars.
const userData = () => ["--user-data-dir", mkdtempSync(join(tmpdir(), "kvsc-"))];
const common = { workspaceFolder: process.env.KRAFT_TEST_WORKSPACE, mocha: { timeout: 180_000 } };

export default defineConfig([
  {
    label: "kraft",
    // In order: the review rejects a gate, the next test approves the one that comes back.
    files: files(["1-review", "2-gates", "3-config"]),
    ...common,
    launchArgs: ["--disable-extensions", ...userData()],
  },
  {
    // Extensions on, with Red Hat YAML installed: the schema path the first run switches off.
    label: "kraft-yaml",
    files: files(["4-yaml"]),
    ...common,
    installExtensions: ["redhat.vscode-yaml"],
    launchArgs: userData(),
  },
]);
