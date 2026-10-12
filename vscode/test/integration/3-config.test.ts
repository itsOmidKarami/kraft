import * as assert from "node:assert";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import * as vscode from "vscode";
import { kraft, until } from "./helpers";

const BROKEN =
  "id: probe\nnodes:\n  - id: run\n    kind: exec\n    tasks:\n      - id: t\n        extends: no_such_task\n";
const FIXED = BROKEN.replace("        extends: no_such_task\n", "        kind: subprocess\n        command: 'true'\n");

suite("config", () => {
  test("a broken chain is flagged at its line before saving, and reloads once fixed", async () => {
    const k = await kraft();
    const file = join(k.locations.templatesDir, "chains", "probe.yaml");
    writeFileSync(file, FIXED);
    const doc = await vscode.workspace.openTextDocument(file);
    const editor = await vscode.window.showTextDocument(doc);

    await editor.edit((e) => e.replace(new vscode.Range(0, 0, doc.lineCount, 0), BROKEN));
    const [diag] = await until("a diagnostic on the extends line", () => {
      const d = k.config!.diagnostics(doc.uri);
      return d.length ? d : undefined;
    });
    assert.equal(diag.range.start.line, 6);
    assert.match(diag.message, /no_such_task/);

    await editor.edit((e) => e.replace(new vscode.Range(0, 0, doc.lineCount, 0), FIXED));
    await until("the diagnostic to clear", () => (k.config!.diagnostics(doc.uri).length === 0 ? true : undefined));

    await doc.save();
    await until("the reload offer", () => (k.config!.lastOffer === "reload" ? true : undefined));
    await k.api.reload();
    const lint = await k.api.lint();
    assert.ok(lint.chains.includes("probe"), "the daemon reports the chain valid");
  });

  test("saving a file the server neither reloads nor restarts for offers nothing", async () => {
    const k = await kraft();
    const file = join(k.locations.templatesDir, "harnesses.yaml");
    const doc = await vscode.workspace.openTextDocument(file);
    const editor = await vscode.window.showTextDocument(doc);
    await editor.edit((e) => e.insert(new vscode.Position(0, 0), "# touched by the config test\n"));
    await doc.save();
    await until("the no-offer verdict", () => (k.config.lastOffer === "none" ? true : undefined));
  });
});
