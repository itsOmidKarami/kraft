import { describe, expect, it } from "vitest";
import type { KraftEvent } from "../../types";
import { age, eventLine, recent } from "./events";

const ev = (type: string, payload: Record<string, unknown> = {}, node_id: string | null = null): KraftEvent => ({ seq: 1, work_item_id: "w", type, payload, node_id, created_at: "t" });

describe("eventLine", () => {
  it.each([
    [ev("node_completed", {}, "plan"), "plan finished"],
    [ev("worker_session_started", { hook_point: "verification.review.code_review" }, "verification"), "code_review started on verification"],
    [ev("worker_session_started", {}, "verification"), "a session started on verification"],
    [ev("gate_approved", { gate: "plan_approval", by: "human" }, "plan_approval"), "plan_approval · approved by you"],
    [ev("gate_approved", { gate: "chain_revision_approval", by: "agent" }), "chain_revision_approval · passed on its own"],
    [ev("fix_cycle_started", { cycle: 1 }, "verification"), "verification · fix loop round 2"],
    [ev("work_item_needs_human", { reason: "needs_context: Allow it?" }, "verification"), "stopped at verification: Allow it?"],
    [ev("mr_closed", { ref: 142, by: "mara" }), "MR !142 closed by mara"],
    [ev("work_item_archived", { by: "auto" }), "archived"],
    [ev("work_item_archived", { by: "auto", kept_branch: "kraft/x", unpushed_commits: 3 }), "archived · kept kraft/x: 3 unpushed commits"],
  ])("%o → %s", (e, line) => expect(eventLine(e)).toBe(line));

  it("leaves bookkeeping out and names a type it does not know", () => {
    expect(eventLine(ev("worker_session_exited"))).toBeNull();
    expect(eventLine(ev("sandbox_oom_killed"))).toBe("sandbox oom killed");
  });
});

describe("recent", () => {
  let seq = 0;
  const at = (type: string, payload: Record<string, unknown> = {}, node_id: string | null = null): KraftEvent => ({ seq: ++seq, work_item_id: "w", type, payload, node_id, created_at: "t" });
  const started = (id: string, hook: string) => at("worker_session_started", { session_id: id, hook_point: hook, node_id: hook === "escalation" ? "verification" : hook.split(".")[0] }, hook === "escalation" ? "verification" : hook.split(".")[0]);
  const exited = (id: string, status: string) => at("worker_session_exited", { session_id: id, status });
  const story = (events: KraftEvent[]) => recent(events).map((r) => r.line);
  it.each([
    ["a live session leads, its node's start folded into it",
      [at("node_started", {}, "verification"), started("s1", "verification.review.code_review")],
      ["code_review is running on verification"]],
    ["a session that ended well drops out, the next one leads",
      [at("node_started", {}, "verification"), started("s1", "verification.checks.lint"), exited("s1", "done"), started("s2", "verification.review.code_review")],
      ["code_review is running on verification"]],
    ["a failed session says so at its end",
      [started("s1", "verification.checks.lint"), exited("s1", "failed")],
      ["lint failed on verification"]],
    ["the node's finish takes the place of what ran in it; the fix loop stays",
      [at("node_started", {}, "verification"), started("s1", "verification.checks.lint"), exited("s1", "failed"), at("fix_cycle_started", { cycle: 1 }, "verification"), started("s2", "verification.checks.lint"), exited("s2", "done"), at("node_completed", {}, "verification")],
      ["verification finished", "verification · fix loop round 2"]],
    ["an escalation's turn that ends reads answered",
      [at("escalation_message", { message: "why?" }, "verification"), started("s3", "escalation"), exited("s3", "done")],
      ["escalation answered", "escalation on verification: why?"]],
  ] as [string, KraftEvent[], string[]][])("%s", (_, events, lines) => expect(story(events)).toEqual(lines));
});

describe("age", () => {
  const now = Date.parse("2026-09-13T10:10:00Z");
  it.each([["2026-09-13T10:09:40Z", "now"], ["2026-09-13T10:04:00Z", "6m"], ["2026-09-13T09:00:00Z", "1h 10m"], ["2026-09-13T08:10:00Z", "2h"]])("%s → %s", (iso, out) => expect(age(iso, now)).toBe(out));
});
