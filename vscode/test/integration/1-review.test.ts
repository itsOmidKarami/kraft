import * as assert from "node:assert";
import * as vscode from "vscode";
import { kraft, until } from "./helpers";

suite("review", () => {
  test("comments on the diff reach the agent as one reject note", async () => {
    const k = await kraft();
    const item = await until("an item at a gate", () => k.store.items().find((i) => i.pending_gate));
    const gate = item.pending_gate!;
    assert.ok(k.gates.announcer.announced.has(`${item.id}:gate:${gate}`), "the gate was announced");

    await vscode.commands.executeCommand("kraft.reviewChanges", item.id);
    const opened = await until("the diff to open", () => k.review.lastOpened);
    assert.ok(opened.files.length > 0, "the review lists the branch's files");

    k.review.addDraft(item.id, { file: opened.files[0], line: 1, body: "tighten this" });
    assert.ok(await k.review.submit(item.id, "one fix, then ship"));

    const events = await until("the gate_rejected event", async () => {
      const r = await fetch(`${k.api.base}/api/work-items/${item.id}/events`);
      const evs = (await r.json()) as { type: string; payload: { note?: string } }[];
      return evs.find((e) => e.type === "gate_rejected");
    });
    assert.match(events.payload.note ?? "", /^Review comments:\n- .+:1 — tighten this\n- \(general\) — one fix, then ship$/);
    assert.equal(k.review.drafts(item.id).comments.length, 0, "the draft is cleared after sending");
  });
});
