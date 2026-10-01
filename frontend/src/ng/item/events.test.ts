import { describe, expect, it } from "vitest";
import type { KraftEvent } from "../../types";
import { age, eventLine } from "./events";

const ev = (type: string, payload: Record<string, unknown> = {}, node_id: string | null = null): KraftEvent => ({ seq: 1, work_item_id: "w", type, payload, node_id, created_at: "t" });

describe("eventLine", () => {
  it.each([
    [ev("node_completed", {}, "plan"), "plan finished"],
    [ev("worker_session_started", { hook_point: "verification.review.code_review" }, "verification"), "code_review is running on verification"],
    [ev("gate_approved", { gate: "plan_approval", by: "human" }, "plan_approval"), "plan_approval · approved by you"],
    [ev("gate_approved", { gate: "chain_revision_approval", by: "agent" }), "chain_revision_approval · passed on its own"],
    [ev("fix_cycle_started", { cycle: 1 }, "verification"), "verification · fix loop round 2"],
    [ev("work_item_needs_human", { reason: "needs_context: Allow it?" }, "verification"), "stopped at verification: Allow it?"],
    [ev("mr_closed", { ref: 142, by: "mara" }), "MR !142 closed by mara"],
  ])("%o → %s", (e, line) => expect(eventLine(e)).toBe(line));

  it("leaves bookkeeping out and names a type it does not know", () => {
    expect(eventLine(ev("worker_session_exited"))).toBeNull();
    expect(eventLine(ev("sandbox_oom_killed"))).toBe("sandbox oom killed");
  });
});

describe("age", () => {
  const now = Date.parse("2026-09-13T10:10:00Z");
  it.each([["2026-09-13T10:09:40Z", "now"], ["2026-09-13T10:04:00Z", "6m"], ["2026-09-13T09:00:00Z", "1h 10m"], ["2026-09-13T08:10:00Z", "2h"]])("%s → %s", (iso, out) => expect(age(iso, now)).toBe(out));
});
