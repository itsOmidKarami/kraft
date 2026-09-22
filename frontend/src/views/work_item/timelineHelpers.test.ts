// @vitest-environment node
import { describe, expect, it } from "vitest";
import { buildItem } from "../../../sweep/fixtures";
import { streamRows } from "./RightPane/Events";
import { detailOf, fallbackSentence, findingsOf, groupByNode, nodeRounds, roundsOf, taskRunLabel, titleOf, verdictWord } from "./timelineHelpers";
import type { KraftEvent, WorkerSession } from "../../types";

const ev = (over: Partial<KraftEvent>): KraftEvent =>
  ({ seq: 1, work_item_id: "w1", type: "node_started", payload: {}, created_at: "t", ...over }) as KraftEvent;

describe("timelineHelpers: judge_verdict", () => {
  it("titles a continue verdict as judge: continuing", () => {
    const e = ev({ type: "judge_verdict", payload: { verdict: "continue", reasoning: "shrinking" } });
    expect(titleOf(e, new Map())).toBe("judge: continuing");
  });

  it("titles stop_needs_human and stop_downgrade both as judge: stopped early", () => {
    const stopped = ev({ type: "judge_verdict", payload: { verdict: "stop_needs_human", reasoning: "recurring" } });
    const downgraded = ev({ type: "judge_verdict", payload: { verdict: "stop_downgrade", reasoning: "not worth it" } });
    expect(titleOf(stopped, new Map())).toBe("judge: stopped early");
    expect(titleOf(downgraded, new Map())).toBe("judge: stopped early");
  });

  it("shows the judge's reasoning as the detail line", () => {
    const e = ev({ type: "judge_verdict", payload: { verdict: "continue", reasoning: "two findings cleared" } });
    expect(detailOf(e)).toBe("two findings cleared");
  });
});

describe("timelineHelpers: plan_progress", () => {
  it("titles a plan_progress event with the plan task's own title, no bare 'task' noun", () => {
    const e = ev({ type: "plan_progress", payload: { node_id: "implementation", task: 3, total: 6, title: "wire the thing" } });
    expect(titleOf(e, new Map())).toBe("wire the thing");
    expect(detailOf(e)).toBe("3 of 6");
  });
});

describe("timelineHelpers: work_item_blocked_by_dependency", () => {
  it("shows the blocker ids as the detail line", () => {
    const e = ev({
      type: "work_item_blocked_by_dependency",
      payload: { node_id: "plan", blocked_by: ["Kraft-abc", "Kraft-def"] },
    });
    expect(detailOf(e)).toBe("blocked on Kraft-abc, Kraft-def");
  });
});

describe("timelineHelpers: paused_by_broken_base", () => {
  it("names the item that broke the base and the follow-up bead", () => {
    const e = ev({
      type: "paused_by_broken_base",
      payload: { broken_by: "w1", follow_up_bead: "Kraft-abc" },
    });
    expect(detailOf(e)).toBe("paused: w1 broke the base it rebased onto (Kraft-abc)");
  });

  it("still renders when no follow-up bead was filed", () => {
    const e = ev({
      type: "paused_by_broken_base",
      payload: { broken_by: "w1", follow_up_bead: null },
    });
    expect(detailOf(e)).toBe("paused: w1 broke the base it rebased onto");
  });
});

describe("timelineHelpers: read_only_violated (Kraft-b8v9a)", () => {
  it("names the scope and the changed files as the detail line", () => {
    const e = ev({
      type: "read_only_violated",
      payload: { node_id: "verify", scope: "verify", files: ["a.py", "b.py"] },
    });
    expect(detailOf(e)).toBe("verify changed: a.py, b.py");
  });
});

describe("timelineHelpers: loop_counters_reset", () => {
  it("names the rebase and which nodes are re-running", () => {
    const e = ev({
      type: "loop_counters_reset",
      payload: { nodes: ["verify", "pre_mr_rebase"], reason: "rebase_bounce", bounce_to: "verify" },
    });
    expect(detailOf(e)).toBe("rebased onto latest main → re-running verify, pre_mr_rebase");
  });
});

describe("timelineHelpers: groupByNode", () => {
  const ev = (seq: number, type: string, payload: Record<string, unknown> = {}): KraftEvent =>
    ({ seq, work_item_id: "w1", type, payload, created_at: `2026-09-14T10:00:0${seq}Z` }) as KraftEvent;

  it("names the pre-node group 'created' and keeps the terminal event in the last node's group (W8.3; e2e chain/lifecycle read it there)", () => {
    const groups = groupByNode([
      ev(1, "work_item_created"),
      ev(2, "node_started", { node_id: "implement" }),
      ev(3, "node_completed", { node_id: "implement" }),
      ev(4, "work_item_completed"),
    ]);
    expect(groups.map((g) => g.node)).toEqual(["implement", "created"]);
    expect(groups[0].events.map((e) => e.type)).toContain("work_item_completed");
    expect(groups.some((g) => g.node === "—")).toBe(false);
  });
});

/* ── W13 · E ─────────────────────────────────────────────────────────────── */

const at = (min: number, sec = 0) => new Date(Date.UTC(2026, 0, 1, 0, min, sec)).toISOString();
const wev = (seq: number, type: string, when: string, payload: Record<string, unknown> = {}): KraftEvent => ({
  seq,
  work_item_id: "w1",
  type,
  created_at: when,
  payload: { node_id: "verify", ...payload },
});

describe("roundsOf (W13 · E)", () => {
  it("the capped fixture item (3 fix cycles) is 4 rounds, opened by node_started and each fix_cycle_started, closed by the judge", () => {
    const { events, sessions } = buildItem("capped", 7, "default");
    const verify = groupByNode(events as KraftEvent[]).find((g) => g.node === "verify")!.events;
    const rounds = roundsOf(verify, (sessions as WorkerSession[]).filter((s) => s.node_id === "verify"));
    expect(rounds).toHaveLength(4);
    const opens = verify
      .filter((e) => e.type === "node_started" || e.type === "fix_cycle_started")
      .map((e) => e.created_at)
      .sort();
    expect(rounds.map((r) => r.startedAt)).toEqual(opens);
    // a round ends at its verdict or where the next one begins, never after it
    for (let i = 0; i < 3; i++) expect(rounds[i].endedAt! <= rounds[i + 1].startedAt).toBe(true);
    expect(rounds.map((r) => r.verdict)).toEqual([null, "continue", "continue", null]);
    expect(rounds.map((r) => r.findings.length)).toEqual([0, 1, 1, 1]);
    // every session sits in the round its created_at falls in
    for (const r of rounds) for (const s of r.sessions) expect(s.round).toBe(r.n);
    expect(rounds.slice(1).every((r) => r.sessions.length === 1)).toBe(true);
  });

  it("groups escalation sessions on their own, never inside a round", () => {
    const { events, sessions } = buildItem("escalated", 3, "long");
    const nr = nodeRounds("review", events as KraftEvent[], sessions as WorkerSession[]);
    const threads = nr.entries.filter((e) => e.kind === "escalationThread");
    // The `long` fixture: thread 1 (3 turns), thread 2 (1 turn) -- Kraft-dkb6g.
    expect(threads).toHaveLength(2);
    expect(threads.map((t) => (t.kind === "escalationThread" ? t.sessions.length : 0))).toEqual([3, 1]);
    expect(nr.rounds.flatMap((r) => r.sessions).some((s) => s.hook_point === "escalation")).toBe(false);
  });

  it("nodeRounds groups escalation sessions by thread, not one entry per turn (Kraft-dkb6g)", () => {
    const s = (over: Partial<WorkerSession>): WorkerSession =>
      ({
        id: "s",
        work_item_id: "w1",
        node_id: "n",
        hook_point: "escalation",
        status: "done",
        attempt: 1,
        thread: 1,
        round: 0,
        created_at: "t",
        started_at: null,
        exited_at: null,
        tokens_in: null,
        tokens_out: null,
        cost_usd: null,
        wall_ms: null,
        model: null,
        head_sha: null,
        ...over,
      }) as WorkerSession;
    const sessions = [
      s({ id: "s1", thread: 1, created_at: "2026-01-01T00:00:00Z" }),
      s({ id: "s2", thread: 1, created_at: "2026-01-01T00:05:00Z" }),
      s({ id: "s3", thread: 2, created_at: "2026-01-01T00:10:00Z" }),
    ];
    const nr = nodeRounds("n", [], sessions);
    const threads = nr.entries.filter((e) => e.kind === "escalationThread");
    expect(threads).toHaveLength(2);
    expect(threads[0]).toMatchObject({ thread: 1, sessions: [sessions[0], sessions[1]] });
    expect(threads[1]).toMatchObject({ thread: 2, sessions: [sessions[2]] });
  });

  it("reads every stop… verdict as stop", () => {
    expect(verdictWord("stop_needs_human")).toBe("stop");
    expect(verdictWord("stop_downgrade")).toBe("stop");
    expect(verdictWord("continue")).toBe("continue");
  });
});

describe("nodeRounds entries (W14 · A)", () => {
  it("lists rounds, not what happened inside them, and drops lifecycle events at a round's edge", () => {
    const events = [
      wev(1, "node_started", at(0)),
      wev(2, "plan_progress", at(1), { task: 1, total: 3 }), // inside round 1
      wev(3, "judge_verdict", at(5), { verdict: "continue" }),
      wev(4, "gate_requested", at(10), { gate: "code_review" }), // outside every round
      wev(9, "gate_approved", at(5), { gate: "spec_approval" }), // at round 1's end: the node's
      wev(5, "fix_cycle_started", at(20)),
      wev(6, "judge_verdict", at(25), { verdict: "stop" }),
      wev(7, "node_completed", at(25, 40)), // 40s after round 2's edge
      wev(8, "node_completed", at(40)), // a minute and more from any edge: a row
    ];
    const nr = nodeRounds("verify", events, []);
    expect(nr.entries.map((e) => (e.kind === "event" ? e.event.seq : e.kind))).toEqual(["round", 9, 4, "round", 8]);
  });
});

describe("streamRows (W13 · E)", () => {
  it("collapses a session's created, started and exited events into one row", () => {
    const rows = streamRows([
      wev(1, "worker_session_created", at(0), { session_id: "s1", hook_point: "on.test.run" }),
      wev(2, "worker_session_started", at(0, 5), { session_id: "s1" }),
      wev(3, "worker_session_exited", at(1, 2), { session_id: "s1", status: "done" }),
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ kind: "session", run: { id: "s1", hook: "on.test.run", started: at(0, 5), exited: at(1, 2), status: "done" } });
  });

  it("collapses a run of plan_progress into one row", () => {
    const rows = streamRows([
      wev(1, "plan_progress", at(0), { task: 3, total: 6 }),
      wev(2, "plan_progress", at(0, 20), { task: 4, total: 6 }),
      wev(3, "plan_progress", at(0, 40), { task: 6, total: 6 }),
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0].kind).toBe("tasks");
    if (rows[0].kind === "tasks") expect(taskRunLabel(rows[0].first, rows[0].last)).toBe("3 → 6 of 6");
  });

  it("puts a waiting row only over a gap longer than two minutes", () => {
    const rows = streamRows([
      wev(1, "node_started", at(0)),
      wev(2, "gate_requested", at(2), { gate: "code_review" }), // exactly 2m: no gap
      wev(3, "gate_approved", at(6, 30), { gate: "code_review" }), // 4m 30s: a gap
    ]);
    expect(rows.map((r) => r.kind)).toEqual(["event", "gate", "gap", "gate"]);
    const gap = rows[2];
    expect(gap.kind === "gap" && gap.ms).toBe(4.5 * 60_000);
  });
});

describe("timelineHelpers: findingsOf and an overridden severity", () => {
  const measured = (findings: unknown[]) =>
    ev({ type: "findings_measured", payload: { node_id: "verify", cycle: 1, findings } });

  it("carries reported_severity when the fix loop overrode the reviewer", () => {
    // Kraft-s7c04.3: a repeat cannot be re-rated down on a tree nobody
    // touched, and the reviewer's own answer is kept rather than erased -- a
    // record only reachable by SQL is the silent refusal this rejects.
    const [f] = findingsOf(
      measured([
        {
          severity: "important",
          message: "swallowed OSError",
          file: "a.py",
          line: 1,
          source_plugin: "code-review",
          reported_severity: "minor",
        },
      ]),
    );
    expect(f.severity).toBe("important");
    expect(f.reported_severity).toBe("minor");
  });

  it("leaves reported_severity undefined when the reviewer was not overridden", () => {
    const [f] = findingsOf(
      measured([{ severity: "minor", message: "m", file: null, line: null, source_plugin: "p" }]),
    );
    expect(f.reported_severity).toBeUndefined();
  });
});

describe("timelineHelpers: launch_fallback (Kraft-0a3h8)", () => {
  const at = "2026-09-22T13:40:00+00:00";
  const hhmm = new Date(at).toTimeString().slice(0, 5);
  const opus = { harness: "claude", model: "opus", effort: "high" };
  const sol = { harness: "codex", model: "gpt-5.6-sol", effort: "high" };
  const sentence = (payload: Record<string, unknown>) => detailOf(ev({ type: "launch_fallback", payload }));

  it("reads a switch after a limited launch", () => {
    expect(sentence({ reason: "rate_limit_hit", from: opus, to: sol, resets_at_iso: at, session_id: "s2" })).toBe(
      `Ran on codex / gpt-5.6-sol instead of claude / opus: claude / opus is rate-limited until ${hhmm}.`,
    );
  });

  it("reads a skip from memory", () => {
    expect(sentence({ reason: "known_limited", from: opus, to: sol, resets_at_iso: at, session_id: "s2" })).toBe(
      `Skipped claude / opus (rate-limited until ${hhmm}); started on codex / gpt-5.6-sol.`,
    );
  });

  it("reads an unavailable harness and a list that ran out", () => {
    const codex = { harness: "codex", model: null, effort: null };
    expect(sentence({ reason: "unavailable", detail: "harness disabled", from: codex, to: null, session_id: null })).toBe(
      "Skipped codex (harness disabled); no fallback is left.",
    );
    expect(sentence({ reason: "unavailable", detail: "harness disabled", from: codex, to: opus, session_id: null })).toBe(
      "Skipped codex (harness disabled); trying claude / opus.",
    );
  });

  it("is the same sentence the board marker shows", () => {
    const p = { reason: "rate_limit_hit", from: opus, to: sol, resets_at_iso: at, session_id: "s2" };
    expect(fallbackSentence(p)).toBe(sentence(p));
  });
});
