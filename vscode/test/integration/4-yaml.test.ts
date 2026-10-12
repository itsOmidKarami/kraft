import * as assert from "node:assert";
import { join } from "node:path";
import * as vscode from "vscode";
import { kraft, until } from "./helpers";

suite("yaml schemas", () => {
  test("Red Hat YAML flags an unknown key in policy.yaml from Kraft's schema", async () => {
    assert.ok(vscode.extensions.getExtension("redhat.vscode-yaml"), "redhat.vscode-yaml is installed for this run");
    const k = await kraft();
    const doc = await vscode.workspace.openTextDocument(join(k.locations.templatesDir, "policy.yaml"));
    const editor = await vscode.window.showTextDocument(doc);
    await editor.edit((e) => e.insert(new vscode.Position(doc.lineCount, 0), "\nnot_a_policy_key: 1\n"));
    const line = doc.lineCount - 2;
    const diag = await until("a schema diagnostic from Red Hat YAML", () =>
      vscode.languages.getDiagnostics(doc.uri).find((d) => d.source !== "Kraft · config" && d.range.start.line === line),
    );
    assert.match(diag.source ?? "", /^yaml-schema: Kraft policy/);
    await vscode.commands.executeCommand("workbench.action.revertAndCloseActiveEditor"); // never saved
  });
});
