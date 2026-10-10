import { describe, expect, it } from "vitest";
import type { DisplayStatus, StopKind, WorkItem } from "../../types";
import { detail } from "../item/testkit";
import { glyphOf, reasonTail, rowAction, ticksOf } from "./rowText";

const NOW = Date.parse("2026-09-13T10:00:00Z");
const stop = (kind: StopKind, more: Partial<NonNullable<WorkItem["stop"]>> = {}) => ({ kind, node: "verification", reason: null, resume_at: null, ...more }) as WorkItem["stop"];
const row = (display_status: DisplayStatus, over: Partial<WorkItem> = {}) => detail({ display_status, ...over });

describe("reasonTail", () => {
  it("says a blocked item is waiting on another item", () => {
    expect(reasonTail(row("blocked"), NOW)).toBe("waiting on another item");
  });

  it("says a queued item is waiting for a slot", () => {
    expect(reasonTail(row("queued"), NOW)).toBe("waiting to start");
  });

  it("says one short thing per status and stop kind", () => {
    const cases: [WorkItem, string][] = [
      [row("needs_you", { stop: stop("gate"), pending_gate: "final_review" }), "approve final review"],
      [row("needs_you", { stop: stop("gate"), pending_gate: "spec_approval" }), "approve spec"],
      [row("needs_you", { stop: stop("gate", { node: "chain_revision_approval" }), pending_gate: null }), "approve chain revision"],
      [row("needs_you", { stop: stop("question", { reason: "needs_context: keep the legacy header?" }) }), "agent asks: keep the legacy header?"],
      [row("needs_you", { stop: stop("cap", { reason: "Running time hit its 8h cap" }) }), "Running time hit its 8h cap"],
      // A budget stop's reason is two sentences; the row keeps the first, cents and all.
      [row("needs_you", { stop: stop("budget", { reason: "budget cap reached: $0.035 spent on this work item, cap $0.030. Nothing new was started; a running agent was not interrupted." }) }), "budget cap reached: $0.035 spent on this work item, cap $0.030"],
      [row("needs_you", { stop: stop("conflict") }), "waiting for you at verification"],
      [row("failed", { stop: stop("failed", { node: "merge_request" }) }), "failed at merge request"],
      [row("needs_you", { stop: stop("gate"), pending_gate: "spec-approval" }), "approve spec"],
      [row("failed", { stop: stop("failed", { node: "post-merge-ci2" }) }), "failed at post merge ci2"],
      [row("paused"), "paused at verification"],
      [row("paused", { current_node_id: "draft_merge_request" }), "paused at draft merge request"],
      [row("paused", { current_node_id: null }), "created paused"],
      [row("running"), "verification"],
      [row("running", { progress: { current: 2, total: 3, title: "x" } as WorkItem["progress"] }), "verification · task 2 of 3"],
      // A finished plan the detail kept, on a later node: no task left to count.
      [row("running", { progress: { current: 3, total: 3, title: "x", tasks: [1, 2, 3].map((n) => ({ n, title: "x", state: "done" })) } as WorkItem["progress"] }), "verification"],
      [row("running", { step: { index: 2, count: 3, name: "review", task: "code_review" }, progress: { current: 2, total: 3, title: "x" } as WorkItem["progress"] }), "2 of 3 · review › code_review"],
      [row("running", { step: { index: 2, count: 3 } }), "verification · step 2 of 3"],
      [row("running", { current_node_id: "post_merge_ci" }), "post merge ci"],
      [row("waiting", { stop: stop("rate_limit", { resume_at: "2026-09-13T10:04:00Z" }) }), "verification · retry in 4m"],
      [row("waiting", { stop: stop("wait") }), "waiting at verification"],
      [row("waiting", { stop: stop("wait", { resume_at: "2026-09-13T10:04:00Z" }) }), "verification · next check in 4m"],
      [row("waiting", { current_node_id: "post_merge_ci", stop: stop("wait", { node: undefined, resume_at: "2026-09-13T10:04:00Z" }) }), "post merge ci · next check in 4m"],
      [row("escalated"), "escalation running"],
      [row("done"), "completed"],
      [row("done", { mr_ref: { number: 139, url: "https://forge.example/mr/139" } }), "merged !139"],
      [row("cancelled"), "cancelled"],
    ];
    for (const [i, want] of cases) expect(reasonTail(i, NOW), `${i.display_status} ${i.stop?.kind ?? ""}`).toBe(want);
  });

  it("adds the fallback harness a run is on (GAP §2 #24)", () => {
    expect(reasonTail(row("running", { fallback: { to: "codex" } }), NOW)).toBe("verification · on codex");
  });
});

describe("rowAction", () => {
  it("offers one action per needs-you kind, failed and a mid-chain pause, and none otherwise", () => {
    const a = (i: WorkItem) => rowAction(i);
    expect(a(row("needs_you", { stop: stop("gate"), pending_gate: "final_review" }))).toEqual({ label: "Review to approve", kind: "gate", gate: "final_review" });
    expect(a(row("needs_you", { stop: stop("question") }))).toMatchObject({ label: "Answer", kind: "peek", tab: "overview" });
    expect(a(row("needs_you", { stop: stop("cap") }))).toMatchObject({ label: "Raise cap", kind: "peek", tab: "config" });
    // The row cannot tell a cap the server raises from one it refuses; the peek's banner can.
    expect(a(row("needs_you", { stop: stop("budget") }))).toEqual({ label: "Raise budget", kind: "peek", tab: "config", budget: true });
    expect(a(row("needs_you", { stop: stop("mr_closed") }))).toMatchObject({ label: "Open", kind: "peek" });
    expect(a(row("needs_you", { stop: stop("stuck") }))).toEqual({ label: "Retry…", kind: "peek", tab: "overview" });
    expect(a(row("failed", { stop: stop("failed") }))).toMatchObject({ label: "Retry…", kind: "peek" });
    expect(a(row("paused"))).toEqual({ label: "Resume", kind: "resume" });
    for (const s of ["running", "waiting", "escalated", "done", "cancelled"] as DisplayStatus[]) expect(a(row(s)), s).toBeNull();
    expect(a(row("paused", { current_node_id: null }))).toBeNull();
  });
});

describe("glyphOf and ticksOf", () => {
  it("draws the current node's kind with the status's state", () => {
    expect(glyphOf(row("running"))).toMatchObject({ kind: "exec", state: "current", icon: "layers" });
    expect(glyphOf(row("needs_you", { current_node_id: "plan_approval" }))).toMatchObject({ kind: "gate", state: "amber" });
    expect(glyphOf(row("failed")).state).toBe("failed");
    expect(glyphOf(row("escalated")).state).toBe("esc");
    expect(glyphOf(row("cancelled"))).toMatchObject({ state: "ghost", icon: "ban" });
    expect(glyphOf(row("done"))).toMatchObject({ state: "done", icon: "check" });
    expect(glyphOf(row("archived"))).toMatchObject({ state: "done", icon: "check" });
    expect(glyphOf(row("paused", { current_node_id: null })).state).toBe("todo");
  });

  it("fills ticks up to the current node, the current one hot when the item waits on a person, all once ended, the whole chain still drawn", () => {
    const states = (i: WorkItem) => ticksOf(i).map((t) => t.state[0]).join("");
    expect(states(row("running"))).toBe("ddct");
    expect(states(row("needs_you", { stop: stop("cap") }))).toBe("ddht");
    expect(states(row("done"))).toBe("dddd");
    expect(states(row("archived"))).toBe("dddd");
    expect(ticksOf(row("done")).map((t) => t.gate)).toEqual([false, true, false, false]);
    expect(states(row("paused", { current_node_id: null }))).toBe("tttt");
    expect(ticksOf(row("running")).map((t) => t.gate)).toEqual([false, true, false, false]);
  });
});
