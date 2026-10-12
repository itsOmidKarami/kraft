import * as assert from "node:assert";
import * as vscode from "vscode";
import { kraft, until } from "./helpers";

type Thread = { file_path: string | null; side: string | null; start_line: number | null; end_line: number | null; label: string | null; draft: boolean };

suite("review", () => {
  test("Approve is refused while a must-fix thread is open", async () => {
    const k = await kraft();
    const item = await until("an item at a gate", () => k.store.items().find((i) => i.pending_gate));
    const gate = item.pending_gate!;
    await vscode.commands.executeCommand("kraft.reviewChanges", item.id);
    const opened = await until("the diff to open", () => k.review.lastOpened);

    const t = await k.review.createThread(item.id, opened.files[0], "new", { start: 0, end: 0 }, "must fix this", "must_fix");
    // Every door an approve could leave by, counted: the refusal is proof only if none was called.
    const sent: string[] = [];
    const doors = { approve: k.api.approve, submitReview: k.api.submitReview };
    k.api.approve = (...a) => (sent.push("approve"), doors.approve.apply(k.api, a));
    k.api.submitReview = (...a) => (sent.push("submitReview"), doors.submitReview.apply(k.api, a));
    try {
      // Refused locally: without the refusal this answers read-first (or approves), never must-fix.
      assert.deepStrictEqual(await k.review.submit(item.id, { outcome: "approve" }), { ok: false, why: "must-fix" });
    } finally {
      Object.assign(k.api, doors);
    }
    await vscode.commands.executeCommand("workbench.action.closeQuickOpen");
    assert.deepStrictEqual(sent, [], "no approve left for the server");

    const evs = (await (await fetch(`${k.api.base}/api/work-items/${item.id}/events`)).json()) as { type: string }[];
    assert.ok(!evs.some((e) => e.type === "gate_approved"), "nothing was approved");
    assert.equal(k.store.item(item.id)?.pending_gate, gate, "the gate is still pending");

    const r = await fetch(`${k.api.base}/api/threads/${t.id}`, { method: "DELETE" });
    assert.ok(r.ok, `DELETE /threads answered ${r.status}`);
    await k.review.reload(item.id);
  });

  test("a thread written in VS Code is the server's thread, and Submit sends it", async () => {
    const k = await kraft();
    const item = await until("an item at a gate", () => k.store.items().find((i) => i.pending_gate));
    const gate = item.pending_gate!;
    assert.ok(k.gates.announcer.announced.has(`${item.id}:gate:${gate}`), "the gate was announced");
    const threads = async () => (await (await fetch(`${k.api.base}/api/work-items/${item.id}/threads`)).json()) as Thread[];

    // As the gate document's title bar calls it: with the tab's Uri, not an id.
    await vscode.commands.executeCommand("kraft.reviewChanges", vscode.Uri.parse(`kraft-artifact:/${item.id}/Spec.md`));
    const opened = await until("the diff to open", () => k.review.lastOpened);
    assert.ok(opened.files.length > 0, "the review lists the branch's files");

    await k.review.createThread(item.id, opened.files[0], "new", { start: 0, end: 1 }, "tighten this", "question");
    const [mine] = await threads();
    assert.deepStrictEqual(
      { file_path: mine.file_path, side: mine.side, start_line: mine.start_line, end_line: mine.end_line, label: mine.label, draft: mine.draft },
      { file_path: opened.files[0], side: "new", start_line: 1, end_line: 2, label: "question", draft: true },
    );

    // A draft written elsewhere (the CLI, the web UI) is drawn too, and goes with the review.
    const r = await fetch(`${k.api.base}/api/work-items/${item.id}/threads`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body: "from the cli" }),
    });
    assert.ok(r.ok, `POST /threads answered ${r.status}`);
    await k.review.reload(item.id);
    await until("both threads drawn", () => k.review.drawn(item.id).length === 2 || undefined);

    assert.deepStrictEqual(await k.review.submit(item.id, { outcome: "request_changes", summary: "one fix, then ship" }), { ok: true });
    const rejected = await until("the gate_rejected event", async () => {
      const evs = (await (await fetch(`${k.api.base}/api/work-items/${item.id}/events`)).json()) as { type: string; payload: { note?: string } }[];
      return evs.find((e) => e.type === "gate_rejected");
    });
    const note = rejected.payload.note ?? "";
    assert.ok(note.startsWith("one fix, then ship"), note);
    assert.ok(note.includes("tighten this") && note.includes("from the cli"), note);
    assert.ok((await threads()).every((t) => !t.draft), "every thread was submitted");
  });
});
