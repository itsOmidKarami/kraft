import { describe, expect, it } from "vitest";
import { REREAD_EVENTS, shouldReread } from "../../src/core/rereads";

describe("shouldReread", () => {
  it.each([
    ["work_item_needs_human", true, true],
    ["gate_requested", true, true],
    ["plan_progress", true, true],
    ["worker_session_created", true, true],
    ["worker_session_started", true, true],
    ["launch_fallback", true, true],
    ["work_item_title_edited", true, true],
    ["mr_opened", true, true],
    ["mr_closed", true, true],
    ["mr_reopened", true, true],
    ["chain_loaded", true, true],
    ["work_item_created", true, true],
    // Labels on the merge request: nothing the tree shows.
    ["mr_labels_set", true, false],
    ["node_completed", true, false],
    ["mr_labels_set", false, true],
    ["some_future_event", true, true],
  ])("shouldReread(%s, known=%s) is %s", (type, known, want) => expect(shouldReread(type, known)).toBe(want));

  it("re-reads on every event the web store re-reads on", () => {
    expect(REREAD_EVENTS.size).toBeGreaterThan(0);
    for (const t of REREAD_EVENTS) expect(shouldReread(t, true)).toBe(true);
  });
});
