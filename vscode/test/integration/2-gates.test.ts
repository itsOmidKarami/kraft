import * as assert from "node:assert";
import * as vscode from "vscode";
import { kraft, until } from "./helpers";

suite("gates", () => {
  test("approving after opening the document clears the gate", async () => {
    const k = await kraft();
    // After the reject in 1-review, the fake agent reruns and the gate comes back.
    const item = await until("an item back at a gate", () => k.store.items().find((i) => i.pending_gate), 180_000);
    const gate = item.pending_gate!;
    // The seeded gate (dev/seed.py) has no digest, so this cannot prove the approval is bound
    // to the render; the unit tests in approve.test.ts do.
    await vscode.commands.executeCommand("kraft.openArtifact", item.id);
    assert.equal(vscode.window.activeTextEditor?.document.uri.scheme, "kraft-artifact");
    await vscode.commands.executeCommand("kraft.approve", item.id, gate);
    await until("the gate to clear", () => k.store.item(item.id)?.pending_gate !== gate || undefined);
    assert.notEqual(k.store.item(item.id)?.pending_gate, gate);
  });
});
