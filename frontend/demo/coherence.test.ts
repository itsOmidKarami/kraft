// @vitest-environment node
import { describe, expect, it } from "vitest";
import { buildScenario, withScopes, type ItemBundle, type Scenario } from "../e2e/contract/mocks/fixtures";
import { frozenOf } from "../e2e/contract/mocks/ngChain";
import { AUTO_ESCALATE_MESSAGE, MR_CLOSED_REASON } from "../e2e/contract/mocks/ngItems";
import { isAgentSession, MODEL, nodeTasks, RUNS, runOutput, taskKind, taskRun } from "../e2e/contract/mocks/taskKinds";
import { transitions } from "../e2e/contract/mocks/transitions";

/**
 * The third unit test that holds the demo's data to Kraft itself (`apiShapes.test.ts` holds its keys, `sampleCoverage.test.ts`
 * its states): what a session may carry depends on what its task is. An agent's has a harness, a model, usage and sub-agents;
 * a builtin, subprocess or forge task's has a command's output and none of those (`TaskPane`: "a test command names no harness
 * and no model"). The first describe reads each item's kind off its own frozen chain with a walk of its own, so its rules
 * share no code with the mock; the last holds the helper the mock reads it with.
 */
const kindsOf = (b: ItemBundle) => {
  const out = new Map<string, string>();
  const chain = b.item.materialized_chain ? JSON.parse(b.item.materialized_chain).chain : { nodes: [] };
  /** A home of tasks: its steps' (`<prefix>.<step>.<task>`) and those it lists directly (step `main`). */
  const home = (prefix: string, h: any) => {
    for (const st of h?.steps ?? []) for (const t of st.tasks ?? []) out.set(`${prefix}.${st.id}.${t.id}`, t.kind ?? "agent");
    for (const t of h?.tasks ?? []) out.set(`${prefix}.main.${t.id}`, t.kind ?? "agent");
  };
  for (const n of chain.nodes) {
    home(n.id, n);
    if (n.fix_loop) home(`${n.id}.fix_loop`, n.fix_loop);
    if (n.fix_loop?.judge) out.set(`${n.id}.fix_loop.judge`, n.fix_loop.judge.kind ?? "agent");
    if (n.on_failure) home(`${n.id}.on_failure`, n.on_failure);
    if (n.auto_review) out.set(`${n.id}.auto_review`, n.auto_review.kind);
  }
  return out;
};
/** An escalation turn is an agent's, and so is a task the chain names as one; the older samples carry no chain, and say by their model. */
const agentic = (b: ItemBundle, s: any) => s.hook_point === "escalation" || (kindsOf(b).get(s.hook_point) ?? (s.model ? "agent" : "")) === "agent";
/** The one agent's task that can finish with no agent: review feedback's triage with no bot feedback to read (`dispatch`). */
const TRIAGE = "merge_request_feedback.review_feedback.triage";
const noAgentTriage = (b: ItemBundle, s: any) =>
  s.hook_point === TRIAGE && s.status === "done" && s.model == null && s.cost_usd == null && logOf(b, s).map((l) => l.text).join("\n") === "no bot feedback collected; no agent started";
const logOf = (b: ItemBundle, s: any): any[] => b.logs[s.id] ?? [];
const lastSegment = (hook: string) => hook.split(".").at(-1)!;
/** `rule` is a way a session can be wrong; the bundles' offenders, readably, so a failure names the item and task. */
const offenders = (S: Scenario, rule: (b: ItemBundle, s: any) => boolean) =>
  Object.values(S.bundles).flatMap((b) => b.sessions.filter((s) => rule(b, s)).map((s) => `${b.item.title}: ${s.hook_point} (${s.status})`));

const seeded = (seed: (S: Scenario) => void) => { const S = buildScenario("default"); seed(S); return S; };
const SCENARIOS: [string, () => Scenario][] = [
  ["the default sample", () => buildScenario("default")],
  ["the long variant", () => buildScenario("long")],
  ["the many variant", () => buildScenario("many")],
  ["test scopes", () => seeded(withScopes)],
];

describe.each(SCENARIOS)("sessions in %s", (_scenario, build) => {
  const S = build();
  const onTheBoard = new Set([...S.ngBoard, ...S.ngArchived, ...Object.values(S.ng)]);
  it.each<[string, (b: ItemBundle, s: any) => boolean]>([
    ["a command's session reports no model", (b, s) => !agentic(b, s) && s.model != null],
    ["a command's session reports no harness", (b, s) => !agentic(b, s) && s.harness != null],
    ["a command's session reports no usage", (b, s) => !agentic(b, s) && (s.tokens_in != null || s.tokens_out != null || s.cost_usd != null)],
    ["a command's session wrote no summary", (b, s) => !agentic(b, s) && s.session_summary_ref != null],
    // `item_paths`: a session's summary is in its item's folder, `items/<id>/sessions/<folder>/out/summary.md`.
    ["a session's summary is in its item's folder", (b, s) => s.session_summary_ref != null && !new RegExp(`^items/${b.item.id}/sessions/[^/]+/out/summary\\.md$`).test(s.session_summary_ref)],
    ["a command's log holds its output only: no tool call, no agent line", (b, s) => !agentic(b, s) && logOf(b, s).some((l) => l.src !== "stdout" || l.agent)],
    ["a command's finished session reports how long it took", (b, s) => !agentic(b, s) && ["done", "failed"].includes(s.status) && !(s.wall_ms > 0)],
    ["a test or lint run has the command it ran", (_b, s) => ["unit_tests", "lint", "typecheck"].includes(lastSegment(s.hook_point)) && !s.command],
    ["an agent's session reports the claude harness", (b, s) => agentic(b, s) && s.harness !== "claude"],
    ["an agent's finished session reports its model and usage", (b, s) => agentic(b, s) && !noAgentTriage(b, s) && ["done", "done_with_concerns"].includes(s.status) && (s.model == null || s.tokens_in == null || s.cost_usd == null)],
    ["an agent's log has its own lines", (b, s) => agentic(b, s) && !noAgentTriage(b, s) && !logOf(b, s).some((l) => l.src === "agent")],
    ["a board item's agents run the mock's model", (b, s) => onTheBoard.has(b.item.id) && agentic(b, s) && s.model != null && s.model !== MODEL],
  ])("%s", (_rule, rule) => {
    expect(offenders(S, rule)).toEqual([]);
  });
});

/** The nodes each template's chain has: what a real server freezes for `default` and `quick-task`, and the board's own `docs_only`. */
const CHAIN_NODES: Record<string, string[]> = {
  default: ["spec", "spec_approval", "plan", "plan_approval", "chain_revision", "chain_revision_approval", "implementation", "verification", "work_brief", "local_review",
    "describe_merge_request", "draft_merge_request", "merge_request_feedback", "work_item_summary", "final_review", "mark_ready", "external_approval", "merge", "post_merge_ci"],
  "quick-task": ["implementation", "verify"],
  docs_only: ["spec", "spec_approval", "implementation", "work_brief", "draft_merge_request"],
};
/** The items that run a frozen chain (the older samples carry none). */
const chained = (S: Scenario) => Object.values(S.bundles).filter((b) => b.item.materialized_chain);
const defIds = (b: ItemBundle): string[] => (b.item.chain_definition?.nodes ?? []).map((n: any) => n.id);
const frozenIds = (b: ItemBundle): string[] => JSON.parse(b.item.materialized_chain).chain.nodes.map((n: any) => n.id);
/** Whether a scope command passed, as its session's status says: null while it has not finished. */
const passedBy = (status: string) => (status === "done" || status === "done_with_concerns" ? true : status === "failed" ? false : null);
/** `store.create_session` counts a node's attempts at a hook point, an escalation turn's included: the sessions whose attempt is not that count. */
const escalationMiscounts = (b: ItemBundle) => b.sessions.filter((s) => s.hook_point === "escalation").filter((s, k, all) => s.attempt !== all.slice(0, k).filter((x) => x.node_id === s.node_id).length + 1).map((s) => `${s.node_id} attempt ${s.attempt}`);
const cost = (b: ItemBundle) => b.sessions.reduce((a, s) => a + (s.cost_usd ?? 0), 0);
/** How a stop's reason starts, by its kind, where the server words it (`walk`, `mr_poller`, `caps`), and the stops that
 *  have none (`board._stop_payload`: a rate limit, an external wait). A kind not listed is not checked. */
const REASONS: Record<string, (reason: string | null, b: ItemBundle) => boolean> = {
  stuck: (r) => /^(stuck|judge): /.test(r ?? ""),
  question: (r) => /^needs_context: /.test(r ?? ""),
  failed: (r) => /^task failed in node \S+: /.test(r ?? ""),
  conflict: (r) => /^git rebase failed for |hit a rebase conflict in node /.test(r ?? ""),
  cap: (r, b) => r === b.events.filter((e) => e.type === "time_cap_reached").at(-1)?.payload.reason,
  budget: (r) => /^(budget cap reached|budget_usd reached|token budget reached): /.test(r ?? ""),
  mr_closed: (r) => r === MR_CLOSED_REASON,
  rate_limit: (r) => r === null,
  wait: (r) => r === null,
};
const reasonFits = (b: ItemBundle) => (REASONS[b.item.stop.kind] ?? (() => true))(b.item.stop.reason, b);
/** The kinds a stop in the stuck set can have (`walk._Stuck`): a failed task, a spent fix loop, a loop that cannot get on,
 *  a rebase conflict in a fix loop. */
const STUCK_SET = ["stuck", "failed", "cap", "conflict"];

describe.each(SCENARIOS)("items in %s", (_scenario, build) => {
  const S = build();
  it.each<[string, (b: ItemBundle) => unknown]>([
    // Each rule answers what is wrong with the item, or nothing.
    ["every session's hook point is a task of the item's frozen chain, or an escalation turn",
      (b) => b.sessions.filter((s) => s.hook_point !== "escalation" && !kindsOf(b).has(s.hook_point)).map((s) => s.hook_point)],
    ["every session's node is a node of the item's chain", (b) => b.sessions.filter((s) => !defIds(b).includes(s.node_id)).map((s) => s.node_id)],
    ["its chain is its template's, in its definition and its frozen chain",
      (b) => [defIds(b), frozenIds(b)].some((ids) => JSON.stringify(ids) !== JSON.stringify(CHAIN_NODES[b.item.chain_template])) && `template ${b.item.chain_template}`],
    ["the node it stands on is in its chain", (b) => b.item.current_node_id != null && !defIds(b).includes(b.item.current_node_id) && `stands on ${b.item.current_node_id}`],
    ["its summary counts its chain's nodes", (b) => b.item.summary.nodes_total !== defIds(b).length && `counts ${b.item.summary.nodes_total}`],
    // To the cent the UI shows: a tweak that scales costs rounds each one.
    ["what it spent is what its sessions cost", (b) => Math.abs(b.item.budget_cap.spent_usd - cost(b)) >= 0.005 && [b.item.budget_cap.spent_usd, cost(b)]],
    ["a completed item stands on its last node", (b) => b.item.status === "completed" && b.item.current_node_id !== defIds(b).at(-1) && `stands on ${b.item.current_node_id}`],
    ["its events never go back in time", (b) => b.events.some((e, k) => k > 0 && Date.parse(e.created_at) < Date.parse(b.events[k - 1].created_at)) && "out of order"],
    ["every command a changed-test-scope task ran is in `scope_runs`, with how it went",
      (b) => b.sessions.filter((s) => s.command && taskRun(b.item.materialized_chain, s.hook_point) === "kraft.verify_changed_test_scopes")
        .filter((s) => { const r = (b.item.scope_runs ?? []).find((x: any) => x.session_id === s.id); return !r || r.command !== s.command || r.hook_point !== s.hook_point || r.passed !== passedBy(s.status); })
        .map((s) => s.hook_point)],
    ["every session was created by an event", (b) => b.sessions.filter((s) => !b.events.some((e) => e.type === "worker_session_created" && e.payload.session_id === s.id)).map((s) => s.hook_point)],
    // `walk`: the judge is due only once a repair has run, and judges a measurement before the next cycle starts.
    ["a fix loop's judge reads a measurement after a repair, before the next cycle, and names the findings that measurement held",
      (b) => b.events.flatMap((e, k) => {
        if (e.type !== "judge_verdict") return [];
        const before = b.events.slice(0, k);
        const repaired = before.some((x) => x.type === "fix_cycle_finished" && Number(x.payload.cycle) <= Number(e.payload.cycle));
        const next = before.some((x) => x.type === "fix_cycle_started" && Number(x.payload.cycle) > Number(e.payload.cycle));
        const read = before.filter((x) => x.type === "findings_measured" && Number(x.payload.cycle) === Number(e.payload.cycle)).at(-1);
        return repaired && !next && read && JSON.stringify(e.payload.findings) === JSON.stringify(read.payload.findings) ? [] : [`judge at cycle ${e.payload.cycle}`];
      })],
    // A task that fails without writing a findings file reaches the round as one finding (`findings.from_blind_failure`): its hook, no file or line, and the log of each job.
    ["a finding a failed task left is a blind failure: its hook, critical, no file or line, and a job to read",
      (b) => b.events.filter((e) => e.type === "findings_measured").flatMap((e) => (e.payload.findings as any[])
        .filter((f) => !kindsOf(b).has(f.source_plugin) || f.severity !== "critical" || f.file !== null || f.line !== null || !String(f.message).startsWith(`${f.source_plugin} failed\n- `)
          || !("same_as" in f) || !(f.jobs ?? []).length || ((f.jobs as any[]).some((j) => j.label !== f.source_plugin) && !String(f.message).includes("\n\nConfirm the fix with: ")))
        .map((f) => f.source_plugin))],
    ["a finished fix cycle names the head it left", (b) => b.events.filter((e) => e.type === "fix_cycle_finished" && !e.payload.head_sha).map((e) => `cycle ${e.payload.cycle}`)],
    ["its stop's reason is one the walk writes for that kind", (b) => b.item.stop && !reasonFits(b) && `${b.item.stop.kind}: ${b.item.stop.reason}`],
    // `gates.auto_escalate_stuck` answers only a stop in the stuck set, and always with the same message.
    ["an automatic escalation turn answers a stuck stop, in Kraft's words",
      (b) => b.events.flatMap((e, k) => {
        if (e.type !== "escalation_message" || !e.payload.auto) return [];
        const stop = b.events.slice(0, k).filter((x) => x.type === "work_item_needs_human").at(-1);
        return stop?.payload.stuck === true && e.payload.message === AUTO_ESCALATE_MESSAGE ? [] : [`turn ${e.payload.turn}`];
      })],
    ["only a stop in the stuck set is marked stuck", (b) => b.events.filter((e) => e.type === "work_item_needs_human" && e.payload.stuck && !STUCK_SET.includes(String(e.payload.kind))).map((e) => e.payload.kind)],
    // `walk` raises `StopKind.CAP` for a task's time cap (not stuck) and for a spent fix loop (`_Stuck`); the cap it writes after `time_cap_reached` is the first.
    ["a stop that follows a time cap is not marked stuck", (b) => b.events.filter((e, k) => e.type === "work_item_needs_human" && e.payload.stuck && b.events.slice(0, k).some((x) => x.type === "time_cap_reached")).map((e) => e.payload.kind)],
    ["the stop an item shows is the one its last needs-human event wrote", (b) => {
      // A pending gate, a rate limit and an external wait are stops the server reads off other events (`board._stop_payload`).
      if (!b.item.stop || b.item.status !== "needs_human" || ["gate", "rate_limit", "wait"].includes(b.item.stop.kind)) return false;
      const e = b.events.filter((x) => x.type === "work_item_needs_human").at(-1);
      return (!e || e.payload.kind !== b.item.stop.kind || (e.payload.reason ?? null) !== (b.item.stop.reason ?? null)) && `${b.item.stop.kind}: ${b.item.stop.reason}`;
    }],
    // `store.create_session` counts a node's attempts at a hook point, an escalation turn's included.
    ["an escalation session's attempt counts the escalation sessions before it on its node", escalationMiscounts],
    ["a stop's task is never an escalation turn", (b) => b.item.stop?.task === "escalation" && "escalation"],
    // Only Kraft's own cancel writes `mr_closed`; the poller that sees a merge request closed writes a stop.
    ["no merge request is closed but by Kraft's cancel", (b) => b.events.filter((e) => e.type === "mr_closed" && e.payload.by !== "cancel").map((e) => e.payload.by)],
    ["it spent no more than its cap, unless that cap stopped it", (b) => b.item.budget_cap.spent_usd > b.item.budget_cap.cap_usd && b.item.stop?.kind !== "budget" && b.item.budget_cap.spent_usd],
  ])("%s", (_rule, wrong) => {
    const found = chained(S).map((b) => [b.item.title, wrong(b)] as const).filter(([, w]) => (Array.isArray(w) ? w.length > 0 : !!w));
    expect(found).toEqual([]);
  });

  it("no two board items share a title", () => {
    const titles = [...S.ngBoard, ...S.ngArchived].map((id) => S.bundles[id].item.title);
    expect(titles.filter((x, k) => titles.indexOf(x) !== k)).toEqual([]);
  });
});

/** Fresh each time: a door changes the scenario it is given. */
const live = () => {
  const S = buildScenario("default");
  return { S, T: transitions(S) };
};
const byBead = (S: Scenario, bead: string) => [...S.ngBoard, ...S.ngArchived].find((id) => S.bundles[id].item.bead_id === bead)!;
/** The paused item with the sessions it ran after `drop`: its last task, where the next attempt starts, is then another's. */
const pausedWithout = (S: Scenario, drop: string[]) => {
  const b = S.bundles[S.ng.paused];
  b.sessions = b.sessions.filter((s) => !drop.includes(lastSegment(s.hook_point)));
  return b;
};
/** What the sample's own session at `hook` says, to hold a door's session to: the task's command and the output it ends with,
 *  from a finished run of the first command it ran there (a changed-test-scope task runs one session per scope command). */
const sampleAt = (b: ItemBundle, hook: string, not: string) => {
  const first = b.sessions.find((s) => s.hook_point === hook && s.id !== not)!;
  return b.sessions.find((s) => s.hook_point === hook && s.id !== not && s.status === "done" && s.command === first.command)!;
};

describe("sessions the doors make", () => {
  it("a command's task gets a command's session: its command, no usage, and its output grown to all of it", () => {
    const { S, T } = live();
    const id = S.ng.running;
    const b = S.bundles[id];
    const hook = "verification.tests.test_changed_scopes";
    const sid = T.createSession(id, "verification", hook).body.session_id;
    const s = () => b.sessions.find((x) => x.id === sid)!;
    const sample = sampleAt(b, hook, sid);
    expect(s()).toMatchObject({ status: "pending", model: null, harness: null, command: sample.command });
    expect(sample.command).toBeTruthy();
    // A changed-test-scope command is listed in the item's scope runs, unfinished, and its outcome follows its session's.
    const scoped = () => b.item.scope_runs.find((r: any) => r.session_id === sid);
    expect(scoped()).toMatchObject({ hook_point: hook, command: sample.command, passed: null });
    // While it runs it has printed less than when it is done.
    const partial = logOf(b, s()).length;
    T.sessionExited(id, sid, null);
    expect(s()).toMatchObject({ status: "done", model: null, harness: null, tokens_in: null });
    expect(scoped()).toMatchObject({ passed: true, exit_code: 0 });
    expect(logOf(b, s()).every((l) => l.src === "stdout")).toBe(true);
    expect(logOf(b, s()).map((l) => l.text)).toEqual(logOf(b, sample).map((l) => l.text));
    expect(logOf(b, s()).length).toBeGreaterThan(partial);
  });

  it("escalation turns the door starts count their attempts on their node, after the sample's own", () => {
    const { S, T } = live();
    const b = S.bundles[S.ng.failed];
    const had = b.sessions.filter((s) => s.hook_point === "escalation").length;
    expect(T.escalate(b.item.id, "look").status).toBe(200);
    T.stopEscalation(b.item.id);
    expect(T.escalate(b.item.id, "again").status).toBe(200);
    expect(b.sessions.filter((s) => s.hook_point === "escalation")).toHaveLength(had + 2);
    expect(escalationMiscounts(b)).toEqual([]);
  });

  it("an escalation turn on another node counts its attempts from one there", () => {
    const { S, T } = live();
    const b = S.bundles[S.ng.failed];
    expect(T.escalate(b.item.id, "look").status).toBe(200);
    T.stopEscalation(b.item.id);
    b.item.current_node_id = "work_item_summary";
    expect(T.escalate(b.item.id, "and here").status).toBe(200);
    expect(b.sessions.at(-1)).toMatchObject({ hook_point: "escalation", node_id: "work_item_summary", attempt: 1 });
    expect(escalationMiscounts(b)).toEqual([]);
  });

  it("an agent's task gets the claude harness and a log of its own lines, and its model once it reports usage", () => {
    const { S, T } = live();
    const id = S.ng.running;
    const b = S.bundles[id];
    const sid = T.createSession(id, "work_brief", "work_brief.main.author").body.session_id;
    const s = () => b.sessions.find((x) => x.id === sid)!;
    expect(s()).toMatchObject({ harness: "claude", model: null, tokens_in: null, cost_usd: null });
    expect(logOf(b, s()).some((l) => l.src === "agent")).toBe(true);
    T.sessionRunning(id, sid);
    T.sessionExited(id, sid, { model: MODEL, tokens_in: 30_000, tokens_out: 4_000, cost_usd: 0.1 });
    expect(s()).toMatchObject({ harness: "claude", model: MODEL, tokens_in: 30_000 });
  });

  it.each<[string, (S: Scenario) => ItemBundle]>([
    ["a forge call", (S) => S.bundles[S.ng.conflict]],
    ["a test command, which has a command line", (S) => pausedWithout(S, ["code_review"])],
  ])("an escalation turn is an agent's, though the last session was %s", (_name, pick) => {
    const { S, T } = live();
    const b = pick(S);
    const last = b.sessions.at(-1)!;
    expect(agentic(b, last)).toBe(false);
    expect(T.escalate(b.item.id, "Why did it stop?").status).toBe(200);
    const turn = b.sessions.at(-1)!;
    expect(turn).toMatchObject({ hook_point: "escalation", harness: "claude", model: MODEL, command: null, tokens_in: null });
    expect(logOf(b, turn).some((l) => l.src === "agent")).toBe(true);
    expect(logOf(b, turn)).not.toEqual(logOf(b, last));
  });

  it.each<[string, (S: Scenario) => ItemBundle, "agent" | "command"]>([
    ["an agent's task", (S) => S.bundles[S.ng.paused], "agent"],
    ["a test command's task", (S) => pausedWithout(S, ["code_review"]), "command"],
    ["a node never started, with no session to copy", (S) => S.bundles[byBead(S, "kraft-f5d3")], "agent"],
  ])("a steered attempt of %s is that task's own kind", (_name, pick, kind) => {
    const { S, T } = live();
    const b = pick(S);
    const before = b.sessions.at(-1);
    expect(T.resume(b.item.id, "Look at the unused imports.").status).toBe(200);
    const next = b.sessions.at(-1)!;
    expect(next).toMatchObject({ status: "pending", tokens_in: null, cost_usd: null, ...(kind === "agent" ? { harness: "claude" } : { harness: null, command: before!.command }) });
    if (kind === "command") expect(next.command).toBeTruthy();
  });
});

describe("taskKind, and what a real frozen chain holds", () => {
  const chain = (nodes: unknown[]) => JSON.stringify({ chain: { nodes } });
  const REAL = chain([
    { id: "implementation", steps: null, tasks: [{ id: "implement", kind: "agent" }] },
    {
      id: "verification",
      steps: [{ id: "tests", tasks: [{ id: "test_changed_scopes", kind: "builtin" }] }, { id: "review", tasks: [{ id: "code_review", kind: "agent" }] }],
      fix_loop: { tasks: [{ id: "repair", kind: "agent" }], steps: null, judge: { id: "judge", kind: "agent" } },
    },
    {
      id: "merge_request_feedback",
      steps: [{ id: "ci", tasks: [{ id: "await_ci", kind: "forge" }] }],
      on_failure: { tasks: null, steps: [{ id: "sync", tasks: [{ id: "sync_mr", kind: "forge" }] }] },
      fix_loop: { tasks: null, steps: [{ id: "repair", tasks: [{ id: "repair", kind: "agent" }] }, { id: "sync", tasks: [{ id: "sync", kind: "forge" }] }], judge: null },
    },
    { id: "local_review", auto_review: { id: "auto_review", kind: "agent" } },
    { id: "slices", nodes: [{ id: "implementation", steps: null, tasks: [{ id: "implement", kind: "agent" }] }] },
    { id: "spec", steps: [{ id: "write", tasks: [{ id: "spec" }] }] },
  ]);
  it.each([
    ["a task under its step", "verification.review.code_review", "agent"],
    ["a node's own tasks sit under step main", "implementation.main.implement", "agent"],
    ["a command under its step", "verification.tests.test_changed_scopes", "builtin"],
    ["a fix loop's tasks sit under step main", "verification.fix_loop.main.repair", "agent"],
    ["a fix loop's judge", "verification.fix_loop.judge", "agent"],
    ["a fix loop of steps", "merge_request_feedback.fix_loop.sync.sync", "forge"],
    ["a failure handler's step", "merge_request_feedback.on_failure.sync.sync_mr", "forge"],
    ["a gate's reviewer", "local_review.auto_review", "agent"],
    ["a node inside a group", "slices.implementation.main.implement", "agent"],
    ["a task that names no kind is an agent's", "spec.write.spec", "agent"],
    ["a task the chain does not name", "verification.tests.nothing", null],
  ])("%s", (_name, hook, kind) => {
    expect(taskKind(REAL, hook)).toBe(kind);
  });

  it.each([
    ["a node's tasks, steps first", "verification", ["verification.tests.test_changed_scopes", "verification.review.code_review"]],
    ["a node that lists its tasks directly", "implementation", ["implementation.main.implement"]],
    ["a gate has none", "local_review", []],
    ["a node inside a group", "slices.implementation", ["slices.implementation.main.implement"]],
    ["a node the chain lacks", "release", null],
  ])("nodeTasks: %s", (_name, node, paths) => {
    expect(nodeTasks(REAL, node)?.map((t) => t.path) ?? null).toEqual(paths);
  });

  const SHIPPED = frozenOf("default");
  it.each([
    ["a builtin's ref", "verification.tests.test_changed_scopes", "kraft.verify_changed_test_scopes"],
    ["a forge task's target", "draft_merge_request.open.open", "mr.open_draft"],
    ["two tasks that share an id run each its own: external_approval's await", "external_approval.main.await", "mr.external_approval"],
    ["and post_merge_ci's", "post_merge_ci.main.await", "mr.post_merge_ci"],
    ["a fix loop's forge step", "merge_request_feedback.fix_loop.sync.sync", "mr.sync"],
    ["an agent's task runs nothing", "implementation.main.implement", null],
  ])("taskRun on the shipped chain: %s", (_name, hook, run) => {
    expect(taskRun(SHIPPED, hook)).toBe(run);
  });
  it("every builtin and forge task of the shipped chains prints what it ran", () => {
    for (const template of ["default", "quick-task"]) {
      const frozen = frozenOf(template);
      const missing = [...kindsOf({ item: { materialized_chain: frozen } } as ItemBundle)].filter(([, kind]) => kind !== "agent").map(([hook]) => hook).filter((hook) => !RUNS[taskRun(frozen, hook) ?? ""]);
      expect(missing).toEqual([]);
    }
  });

  it.each([
    ["an escalation turn", null, "escalation", false, true],
    ["a task the chain names as a forge call", REAL, "merge_request_feedback.ci.await_ci", true, false],
    ["a path the chain does not name is an agent's, whatever the row says", REAL, "release", false, true],
    ["no frozen chain: the row's model says", null, "x.y.z", true, true],
    ["no frozen chain, no model", null, "x.y.z", false, false],
  ] as const)("isAgentSession: %s", (_name, frozen, hook, hasModel, agent) => {
    expect(isAgentSession(frozen, hook, hasModel)).toBe(agent);
  });
});

describe("what a command prints", () => {
  const MR = "https://example.test/pull/1";
  it.each(Object.keys(RUNS).filter((task) => RUNS[task].failed))("%s: a failed run does not print what a passing one does", (task) => {
    expect(runOutput(task, "failed", MR)).not.toEqual(runOutput(task, "done", MR));
  });
  it.each(Object.keys(RUNS).filter((task) => RUNS[task].partial))("%s: a run in progress has printed less than a finished one", (task) => {
    expect(runOutput(task, "running", MR)).not.toEqual(runOutput(task, "done", MR));
  });
  it.each(["paused", "waiting", "capped_out", "needs_context", "rate_limited"])("a run stopped as %s has printed what a running one has, not the finished output", (status) => {
    for (const task of Object.keys(RUNS).filter((t) => RUNS[t].partial)) expect(runOutput(task, status, MR)).toEqual(runOutput(task, "running", MR));
  });
  it("a forge call's failure names its route as the transport does, with the placeholders as written", () => {
    expect(runOutput("mr.open_draft", "failed", "!1").join("\n")).toContain("POST repos/{owner}/{repo}/pulls: ");
  });

  it.each(Object.keys(RUNS))("%s: a finished run prints something", (task) => {
    expect(runOutput(task, "done", MR).length).toBeGreaterThan(0);
  });
});
