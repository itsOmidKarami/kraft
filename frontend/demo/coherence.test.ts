// @vitest-environment node
import { describe, expect, it } from "vitest";
import { buildScenario, withReviewer, withScopes, type ItemBundle, type Scenario } from "../e2e/contract/mocks/fixtures";
import { isAgentSession, MODEL, nodeTasks, RUNS, runOutput, taskKind } from "../e2e/contract/mocks/taskKinds";
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
  for (const n of chain.nodes) {
    for (const st of n.steps ?? []) for (const t of st.tasks) out.set(`${n.id}.${st.id}.${t.id}`, t.kind);
    if (n.auto_review) out.set(`${n.id}.auto_review`, n.auto_review.kind);
  }
  return out;
};
/** An escalation turn is an agent's, and so is a task the chain names as one; the older samples carry no chain, and say by their model. */
const agentic = (b: ItemBundle, s: any) => s.hook_point === "escalation" || (kindsOf(b).get(s.hook_point) ?? (s.model ? "agent" : "")) === "agent";
const logOf = (b: ItemBundle, s: any): any[] => b.logs[s.id] ?? [];
const lastSegment = (hook: string) => hook.split(".").at(-1)!;
/** `rule` is a way a session can be wrong; the bundles' offenders, readably, so a failure names the item and task. */
const offenders = (S: Scenario, rule: (b: ItemBundle, s: any) => boolean) =>
  Object.values(S.bundles).flatMap((b) => b.sessions.filter((s) => rule(b, s)).map((s) => `${b.item.title}: ${s.hook_point} (${s.status})`));

const seeded = (seed: (S: Scenario) => void) => { const S = buildScenario("default"); seed(S); return S; };
const SCENARIOS: [string, () => Scenario][] = [
  ["the default sample", () => buildScenario("default")],
  ["the long variant", () => buildScenario("long")],
  ["a gate's reviewer", () => seeded(withReviewer)],
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
    ["a command's log holds its output only: no tool call, no agent line", (b, s) => !agentic(b, s) && logOf(b, s).some((l) => l.src !== "stdout" || l.agent)],
    ["a command's finished session reports how long it took", (b, s) => !agentic(b, s) && ["done", "failed"].includes(s.status) && !(s.wall_ms > 0)],
    ["a test or lint run has the command it ran", (_b, s) => ["unit_tests", "lint", "typecheck"].includes(lastSegment(s.hook_point)) && !s.command],
    ["an agent's session reports the claude harness", (b, s) => agentic(b, s) && s.harness !== "claude"],
    ["an agent's finished session reports its model and usage", (b, s) => agentic(b, s) && ["done", "done_with_concerns"].includes(s.status) && (s.model == null || s.tokens_in == null || s.cost_usd == null)],
    ["an agent's log has its own lines", (b, s) => agentic(b, s) && !logOf(b, s).some((l) => l.src === "agent")],
    ["a board item's agents run the mock's model", (b, s) => onTheBoard.has(b.item.id) && agentic(b, s) && s.model != null && s.model !== MODEL],
  ])("%s", (_rule, rule) => {
    expect(offenders(S, rule)).toEqual([]);
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
/** What the sample's own session at `hook` says, to hold a door's session to: the task's command and the output it ends with. */
const sampleAt = (b: ItemBundle, hook: string, not: string) => b.sessions.find((s) => s.hook_point === hook && s.id !== not)!;

describe("sessions the doors make", () => {
  it("a command's task gets a command's session: its command, no usage, and its output grown to all of it", () => {
    const { S, T } = live();
    const id = S.ng.running;
    const b = S.bundles[id];
    const hook = "verification.test.unit_tests";
    const sid = T.createSession(id, "verification", hook).body.session_id;
    const s = () => b.sessions.find((x) => x.id === sid)!;
    const sample = sampleAt(b, hook, sid);
    expect(s()).toMatchObject({ status: "pending", model: null, harness: null, command: sample.command });
    expect(sample.command).toBeTruthy();
    // While it runs it has printed less than when it is done.
    const partial = logOf(b, s()).length;
    T.sessionExited(id, sid, null);
    expect(s()).toMatchObject({ status: "done", model: null, harness: null, tokens_in: null });
    expect(logOf(b, s()).every((l) => l.src === "stdout")).toBe(true);
    expect(logOf(b, s()).map((l) => l.text)).toEqual(logOf(b, sample).map((l) => l.text));
    expect(logOf(b, s()).length).toBeGreaterThan(partial);
  });

  it("an agent's task gets the claude harness and a log of its own lines, and its model once it reports usage", () => {
    const { S, T } = live();
    const id = S.ng.running;
    const b = S.bundles[id];
    const sid = T.createSession(id, "work_brief", "work_brief.write.work_brief").body.session_id;
    const s = () => b.sessions.find((x) => x.id === sid)!;
    expect(s()).toMatchObject({ harness: "claude", model: null, tokens_in: null, cost_usd: null });
    expect(logOf(b, s()).some((l) => l.src === "agent")).toBe(true);
    T.sessionRunning(id, sid);
    T.sessionExited(id, sid, { model: MODEL, tokens_in: 30_000, tokens_out: 4_000, cost_usd: 0.1 });
    expect(s()).toMatchObject({ harness: "claude", model: MODEL, tokens_in: 30_000 });
  });

  it.each<[string, (S: Scenario) => ItemBundle]>([
    ["a forge call", (S) => S.bundles[S.ng.failed]],
    ["a test command, which has a command line", (S) => pausedWithout(S, ["lint", "typecheck", "security_scan", "code_review"])],
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
    ["a test command's task", (S) => pausedWithout(S, ["code_review", "typecheck", "security_scan"]), "command"],
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
  it.each(Object.keys(RUNS))("%s: a finished run prints something", (task) => {
    expect(runOutput(task, "done", MR).length).toBeGreaterThan(0);
  });
});
