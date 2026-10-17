import { describe, expect, it } from "vitest";
import type { KraftEvent } from "../../types";
import type { EventType } from "../../types/vocab.generated";
import { age, eventLine, recent } from "./events";

const ev = (type: EventType, payload: Record<string, unknown> = {}, node_id: string | null = null): KraftEvent => ({ seq: 1, work_item_id: "w", type, payload, node_id, created_at: "t" });

describe("eventLine", () => {
  it.each([
    [ev("node_completed", {}, "plan"), "plan finished"],
    [ev("worker_session_started", { hook_point: "verification.review.code_review" }, "verification"), "code_review started on verification"],
    [ev("worker_session_started", {}, "verification"), "a session started on verification"],
    [ev("gate_approved", { gate: "plan_approval", by: "human" }, "plan_approval"), "approve plan · approved by you"],
    [ev("gate_approved", { gate: "chain_revision_approval", by: "agent" }), "approve chain revision · passed on its own"],
    [ev("fix_cycle_started", { cycle: 1 }, "verification"), "verification · fix loop round 2"],
    [ev("fix_cycle_finished", { cycle: 1, committed: true }, "verification"), "verification · fix loop round 2 · the fix committed a change"],
    [ev("fix_cycle_finished", { cycle: 1, committed: false }, "verification"), "verification · fix loop round 2 · the fix changed nothing"],
    [ev("fix_cycle_finished", { cycle: 1, committed: null }, "verification"), "verification · fix loop round 2"],
    [ev("work_item_needs_human", { reason: "needs_context: Allow it?" }, "verification"), "stopped at verification: Allow it?"],
    // The auto-escalation sentence is read whole; the row clamps it on screen.
    [ev("escalation_message", { message: "(Auto-escalated: no one has looked at this yet. Diagnose why it stopped and fix it if you can; if you're not confident, stop and say so instead of guessing.)" }, "verification"), "escalation on verification: (Auto-escalated: no one has looked at this yet. Diagnose why it stopped and fix it if you can; if you're not confident, stop and say so instead of guessing.)"],
    [ev("mr_closed", { ref: 142, by: "mara" }), "MR !142 closed by mara"],
    [ev("work_item_archived", { by: "auto" }), "archived"],
    [ev("work_item_archived", { by: "auto", kept_branch: "kraft/x", unpushed_commits: 3 }), "archived · kept kraft/x: 3 unpushed commits"],
    [ev("beads_left_open", { beads: ["kraft-a", "kraft-b"], reason: "no change behind them; close them by hand" }), "left kraft-a, kraft-b open: no change behind them; close them by hand"],
  ])("%o → %s", (e, line) => expect(eventLine(e)).toBe(line));

  it("names the nodes and gates it is given as the chain does", () => {
    const nodes = [{ id: "spec_approval", kind: "gate", artifact: "spec" }, { id: "merge_request", kind: "exec" }];
    expect(eventLine(ev("gate_requested", { gate: "spec_approval" }, "spec_approval"), nodes)).toBe("approve spec is waiting for you");
    expect(eventLine(ev("node_started", {}, "merge_request"), nodes)).toBe("merge request started");
  });

  it("leaves bookkeeping out and names a type it does not know", () => {
    expect(eventLine(ev("worker_session_exited"))).toBeNull();
    expect(eventLine(ev("sandbox_oom_killed"))).toBe("sandbox oom killed");
  });
});

describe("recent", () => {
  let seq = 0;
  const at = (type: EventType, payload: Record<string, unknown> = {}, node_id: string | null = null): KraftEvent => ({ seq: ++seq, work_item_id: "w", type, payload, node_id, created_at: "t" });
  const started = (id: string, hook: string) => at("worker_session_started", { session_id: id, hook_point: hook, node_id: hook === "escalation" ? "verification" : hook.split(".")[0] }, hook === "escalation" ? "verification" : hook.split(".")[0]);
  const exited = (id: string, status: string) => at("worker_session_exited", { session_id: id, status });
  // `store`'s ends with no exit: a person's stop (pause, cancel, complete, skip, Stop agent) and reattach's lost session.
  const paused = (id: string) => at("worker_session_paused", { session_id: id });
  const unknown = (id: string) => at("session_unknown", { session_id: id, reason: "pid reused" });
  const lost = at("work_item_needs_human", { node_id: "verification", reason: "reattach: running session, PID identity unconfirmed, no result (pid reused)", kind: "infra" }, "verification");
  const story = (events: KraftEvent[]) => recent(events).map((r) => r.line);
  const stamped = (created_at: string, e: KraftEvent): KraftEvent => ({ ...e, created_at });
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
    ["a fix round's outcome joins its start line, whichever way it went",
      [at("fix_cycle_started", { cycle: 1 }, "verification"), at("fix_cycle_finished", { cycle: 1, committed: true }, "verification"),
        at("fix_cycle_started", { cycle: 2 }, "verification"), at("fix_cycle_finished", { cycle: 2, committed: false }, "verification"),
        at("fix_cycle_started", { cycle: 3 }, "verification"), at("fix_cycle_finished", { cycle: 3, committed: null }, "verification")],
      ["verification · fix loop round 4", "verification · fix loop round 3 · the fix changed nothing", "verification · fix loop round 2 · the fix committed a change"]],
    ["a repeated cycle number joins the latest start, not an older round's",
      [at("fix_cycle_started", { cycle: 1 }, "verification"), at("fix_cycle_finished", { cycle: 1, committed: true }, "verification"),
        at("fix_cycle_started", { cycle: 1 }, "verification"), at("fix_cycle_finished", { cycle: 1, committed: false }, "verification")],
      ["verification · fix loop round 2 · the fix changed nothing", "verification · fix loop round 2 · the fix committed a change"]],
    ["a gate's decision takes the place of its request, a pending one stays",
      [at("gate_requested", { gate: "plan_approval" }, "plan_approval"), at("gate_approved", { gate: "plan_approval", by: "human" }, "plan_approval"), at("gate_requested", { gate: "final_review" }, "final_review")],
      ["approve final review is waiting for you", "approve plan · approved by you"]],
    // R14b-05: skipping a gate answers its request as a decision does.
    ["a skipped gate takes the place of its request",
      [at("gate_requested", { gate: "review_gate" }, "review_gate"), at("node_skipped", { node_id: "review_gate", gate: "review_gate" }, "review_gate"), at("work_item_completed")],
      ["finished", "review gate skipped"]],
    // CG-4: a decided gate reads once, and a finished node says how long it ran.
    ["a decided gate's own finish does not repeat its decision",
      [at("gate_requested", { gate: "final_review" }, "final_review"), at("gate_approved", { gate: "final_review", by: "human" }, "final_review"), at("node_completed", {}, "final_review")],
      ["approve final review · approved by you"]],
    ["a finished node says how long it ran",
      [stamped("2026-10-03T10:00:00Z", at("node_started", {}, "verification")), stamped("2026-10-03T11:04:00Z", at("node_completed", {}, "verification"))],
      ["verification finished · 1h 4m"]],
    ["the item's end reads once, in its last node's line",
      [stamped("2026-10-03T10:00:00Z", at("node_started", {}, "release")), stamped("2026-10-03T10:12:00Z", at("node_completed", {}, "release")), at("work_item_completed")],
      ["release finished · 12m"]],
    ["an item completed with no node just finished keeps its own line",
      [stamped("2026-10-03T10:00:00Z", at("node_started", {}, "plan")), stamped("2026-10-03T10:01:00Z", at("node_completed", {}, "plan")), at("pause_requested"), at("work_item_completed")],
      ["finished", "paused", "plan finished · 1m"]],
    ["an escalation's turn that ends reads answered",
      [at("escalation_message", { message: "why?" }, "verification"), started("s3", "escalation"), exited("s3", "done")],
      ["escalation answered", "escalation on verification: why?"]],
    // `store.pause_work_item`: the pause, then each session it stopped, with no exit after (`session_exited` writes none for a paused row).
    ["a session a pause stopped drops out, the pause tells it",
      [at("node_started", {}, "verification"), started("s1", "verification.review.code_review"), at("pause_requested", { sessions: ["s1"] }), paused("s1")],
      ["paused"]],
    // A resumed task is a new session (`dispatch._launch_agent`): its own start, after `agent_session_resumed` names the paused one.
    ["a paused task that resumes runs as a new session",
      [started("s1", "verification.review.code_review"), at("pause_requested", { sessions: ["s1"] }), paused("s1"),
        at("work_item_resumed"), at("agent_session_resumed", { task: "verification.review.code_review", session_id: "s1" }, "verification"), started("s2", "verification.review.code_review")],
      ["code_review is running on verification", "resumed", "paused"]],
    ["a resumed session that ends well drops out like any other",
      [started("s1", "verification.review.code_review"), at("pause_requested", { sessions: ["s1"] }), paused("s1"),
        at("work_item_resumed"), at("agent_session_resumed", { task: "verification.review.code_review", session_id: "s1" }, "verification"), started("s2", "verification.review.code_review"), exited("s2", "done")],
      ["resumed", "paused"]],
    // `store.end_work_item`: the sessions stopped first, then the end.
    ["a session a cancel stopped drops out, the cancel tells it",
      [started("s1", "verification.review.code_review"), paused("s1"), at("work_item_cancelled", { reason: "superseded", node_id: "verification" }, "verification")],
      ["cancelled"]],
    // `store.skip_node`: the skip, then the sessions it stopped.
    ["a session a skip stopped drops out, the skip tells it",
      [at("node_started", {}, "verification"), started("s1", "verification.checks.lint"), at("node_skipped", { node_id: "verification", gate: null }, "verification"), paused("s1")],
      ["verification skipped"]],
    // `store.stop_escalation_session` (Stop agent): the turn ends, the item stays as it stopped.
    ["an escalation's turn a person stopped reads stopped",
      [at("escalation_message", { message: "why?" }, "verification"), started("s3", "escalation"), paused("s3")],
      ["escalation stopped", "escalation on verification: why?"]],
    ["a retry after a stopped escalation turn reads over it",
      [at("escalation_message", { message: "why?" }, "verification"), started("s3", "escalation"), paused("s3"), at("work_item_retried", {}, "verification")],
      ["retried verification", "escalation stopped", "escalation on verification: why?"]],
    // `store.skip_scope`: the sessions it stopped, then the skip.
    ["a session a scope skip stopped drops out",
      [started("s1", "verification.checks.lint"), paused("s1"), at("scope_skipped", { path: "verification.checks.lint" })],
      ["scope skipped"]],
    // A session ends once: a stop racing a turn that just finished, or a late exit after a stop, adds no second end.
    ["a stop after the turn's exit adds nothing",
      [started("s3", "escalation"), exited("s3", "done"), paused("s3")],
      ["escalation answered"]],
    ["a failed exit after a stop adds nothing",
      [started("s1", "verification.checks.lint"), at("pause_requested", { sessions: ["s1"] }), paused("s1"), exited("s1", "failed")],
      ["paused"]],
    // `store.session_running` appends its start even when a pause already settled the row.
    ["a start after the session was stopped adds nothing",
      [at("pause_requested", { sessions: ["s1"] }), paused("s1"), started("s1", "verification.review.code_review")],
      ["paused"]],
    // `reattach`: a session a restart could not confirm, then the stop that names why.
    ["a session a restart could not confirm says so",
      [started("s1", "verification.checks.lint"), unknown("s1"), lost],
      ["stopped at verification: reattach: running session, PID identity unconfirmed, no result (pid reused)", "lint was lost in a restart on verification"]],
    ["an escalation's turn lost in a restart says so",
      [started("s3", "escalation"), unknown("s3")],
      ["escalation was lost in a restart"]],
    ["a session lost in a restart goes, as a failed one does, once its node finishes",
      [started("s1", "verification.checks.lint"), unknown("s1"), lost, at("work_item_retried", {}, "verification"), started("s2", "verification.checks.lint"), exited("s2", "done"), at("node_completed", {}, "verification")],
      ["verification finished", "retried verification", "stopped at verification: reattach: running session, PID identity unconfirmed, no result (pid reused)"]],
  ] as [string, KraftEvent[], string[]][])("%s", (_, events, lines) => expect(story(events)).toEqual(lines));
});

describe("age", () => {
  const now = Date.parse("2026-09-13T10:10:00Z");
  it.each([["2026-09-13T10:09:40Z", "now"], ["2026-09-13T10:04:00Z", "6m"], ["2026-09-13T09:00:00Z", "1h 10m"], ["2026-09-13T08:10:00Z", "2h"]])("%s → %s", (iso, out) => expect(age(iso, now)).toBe(out));
});
