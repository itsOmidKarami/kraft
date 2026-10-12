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
    // It opens rendered, in the markdown preview, and the tab names the item.
    const tab = await until("the preview tab", () => {
      const t = vscode.window.tabGroups.activeTabGroup.activeTab;
      return t?.input instanceof vscode.TabInputWebview && t.input.viewType.endsWith("markdown.preview") ? t : undefined;
    });
    assert.ok(tab.label.endsWith(`(${item.id}).md`), tab.label);
    // As the preview's title bar calls it: no id, so the item is the one the preview shows.
    await vscode.commands.executeCommand("kraft.approve");
    await until("the gate to clear", () => k.store.item(item.id)?.pending_gate !== gate || undefined);
    assert.notEqual(k.store.item(item.id)?.pending_gate, gate);
  });
});
