import { describe, expect, it } from "vitest";
import type { DisplayStatus, StopKind, WorkItem } from "../../types";
import { detail } from "../item/testkit";
import { admittedRowAction, displayName, gateName, glyphOf, nameOf, reasonTail, rowAction, ticksOf } from "./rowText";

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
    // The server's `launch_fallback` payload: `to` is the candidate it switched to.
    expect(reasonTail(row("running", { fallback: { to: { harness: "codex", model: "gpt-5.6-sol", effort: null } } }), NOW)).toBe("verification · on gpt-5.6-sol");
    expect(reasonTail(row("running", { fallback: { to: { harness: "codex", model: null, effort: null } } }), NOW)).toBe("verification · on codex");
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

describe("admittedRowAction: rowAction, only through a door the server's actions list (F4)", () => {
  const raise = { label: "Raise budget", kind: "peek", tab: "config", budget: true } as const;
  it.each<[string, WorkItem, ReturnType<typeof rowAction>]>([
    ["an older server: rowAction as it is", row("paused"), { label: "Resume", kind: "resume" }],
    ["paused, resume listed", row("paused", { actions: ["resume", "steer"] }), { label: "Resume", kind: "resume" }],
    ["paused, the server refusing resume", row("paused", { actions: ["steer", "cancel"] }), null],
    ["failed, the server refusing retry", row("failed", { stop: stop("failed"), actions: ["escalate", "cancel"] }), null],
    ["stuck, the server refusing retry", row("needs_you", { stop: stop("stuck"), actions: ["escalate"] }), null],
    ["a question, the server refusing resume", row("needs_you", { stop: stop("question"), actions: ["steer"] }), null],
    ["a cap, the server refusing retry", row("needs_you", { stop: stop("cap"), actions: ["cancel"] }), null],
    // The peek offers Retry when it can't raise: the row still goes there.
    ["a budget, retry without raise_budget", row("needs_you", { stop: stop("budget"), actions: ["retry"] }), raise],
    ["a budget, neither", row("needs_you", { stop: stop("budget"), actions: ["cancel"] }), null],
    ["a gate, reject alone", row("needs_you", { stop: stop("gate"), pending_gate: "final_review", actions: ["reject"] }), { label: "Review to approve", kind: "gate", gate: "final_review" }],
    ["a gate, the server refusing both", row("needs_you", { stop: stop("gate"), pending_gate: "final_review", actions: ["cancel"] }), null],
    // Open goes to the peek, which decides its own doors.
    ["any other stop, nothing listed", row("needs_you", { stop: stop("mr_closed"), actions: [] }), { label: "Open", kind: "peek", tab: "overview" }],
  ])("%s", (_name, item, want) => expect(admittedRowAction(item)).toEqual(want));
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
    expect(glyphOf(row("blocked", { current_node_id: null })).state).toBe("todo");
    expect(glyphOf(row("blocked")).state).toBe("current");
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

describe("displayName", () => {
  it.each([
    ["a gate, by the document it decides", { id: "spec_approval", kind: "gate", artifact: "spec" }, "approve spec"],
    ["a gate whose artifact is not its id's prefix", { id: "final_review", kind: "gate", artifact: "review_brief" }, "approve review brief"],
    ["a gate with a longer id", { id: "chain_revision_approval", kind: "gate", artifact: "chain_revision" }, "approve chain revision"],
    ["a gate deciding no document, by its id", { id: "final_review", kind: "gate" }, "approve final review"],
    ["an exec node whose id ends in approval, in words", { id: "external_approval", kind: "exec" }, "external approval"],
    ["any other node, in words", { id: "merge_request", kind: "exec" }, "merge request"],
  ])("names %s", (_, node, name) => {
    expect(displayName(node)).toBe(name);
  });

  it("finds the kind and artifact in the chain, and falls back to the id in words", () => {
    const nodes = [{ id: "plan_approval", kind: "gate", artifact: "plan" }];
    expect(nameOf(nodes, "plan_approval")).toBe("approve plan");
    expect(nameOf(nodes, "open_draft")).toBe("open draft");
    expect(gateName(nodes, "plan_approval")).toBe("approve plan");
    expect(gateName(nodes, "spec_approval")).toBe("approve spec");
  });
});

describe("a gate row", () => {
  it("is named by its artifact on the board too", () => {
    const nodes = [{ id: "final_review", kind: "gate", artifact: "review_brief" }] as WorkItem["chain_definition"]["nodes"];
    const r = row("needs_you", { stop: stop("gate", { node: "final_review" }), pending_gate: "final_review", chain_definition: { nodes } as WorkItem["chain_definition"] });
    expect(reasonTail(r, NOW)).toBe("approve review brief");
  });
});
