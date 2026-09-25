import * as assert from "node:assert";
import * as vscode from "vscode";
import { kraft, until } from "./helpers";

suite("gates", () => {
  test("approving from VS Code clears the gate", async () => {
    const k = await kraft();
    // After the reject in 1-review, the fake agent reruns and the gate comes back.
    const item = await until("an item back at a gate", () => k.store.items().find((i) => i.pending_gate), 180_000);
    const gate = item.pending_gate!;
    await vscode.commands.executeCommand("kraft.approve", item.id, gate);
    await until("the gate to clear", () => k.store.item(item.id)?.pending_gate !== gate || undefined);
    assert.notEqual(k.store.item(item.id)?.pending_gate, gate);
  });
});
