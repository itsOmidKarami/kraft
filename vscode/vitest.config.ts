import { join } from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // The extension host's module, for the glue a unit test drives (src/board.ts).
  resolve: { alias: { vscode: join(__dirname, "test/unit/fixtures/vscode.ts") } },
  test: { include: ["test/unit/**/*.test.ts"], environment: "node" },
});
