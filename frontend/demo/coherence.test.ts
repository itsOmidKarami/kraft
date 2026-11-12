// @vitest-environment node
import { describe, expect, it } from "vitest";
import { buildScenario, type ItemBundle, type Scenario } from "../e2e/contract/mocks/fixtures";
import { isAgentSession, MODEL, taskKind } from "../e2e/contract/mocks/taskKinds";
import { transitions } from "../e2e/contract/mocks/transitions";

/**
 * The third unit test that holds the demo's data to Kraft itself (`apiShapes.test.ts` holds its keys, `sampleCoverage.test.ts`
 * its states): what a session may carry depends on what its task is. An agent's has a harness, a model, usage and sub-agents;
 * a builtin, subprocess or forge task's has a command's output and none of those (`TaskPane`: "a test command names no harness
 * and no model"). Read here off each item's own frozen chain, by a walk of its own so this test shares no code with the mock.
 */
const kindsOf = (b: ItemBundle) => {
  const out = new Map<string, string>();
  const chain = b.item.materialized_chain ? JSON.parse(b.item.materialized_chain).chain : { nodes: [] };
  for (const n of chain.nodes) for (const st of n.steps ?? []) for (const t of st.tasks) out.set(`${n.id}.${st.id}.${t.id}`, t.kind);
  return out;
};
const agentic = (b: ItemBundle, s: any) => s.hook_point === "escalation" || (kindsOf(b).get(s.hook_point) ?? (s.model ? "agent" : "")) === "agent";
const logOf = (b: ItemBundle, s: any): any[] => b.logs[s.id] ?? [];
/** `rule` is a way a session can be wrong; the bundles' offenders, readably, so a failure names the item and task. */
const offenders = (S: Scenario, rule: (b: ItemBundle, s: any) => boolean) =>
  Object.values(S.bundles).flatMap((b) => b.sessions.filter((s) => rule(b, s)).map((s) => `${b.item.title}: ${s.hook_point} (${s.status})`));

const SAMPLE = buildScenario("default");
const THE_ONES_ON_THE_BOARD = new Set([...SAMPLE.ngBoard, ...SAMPLE.ngArchived, ...Object.values(SAMPLE.ng)]);

describe("the built scenario's sessions", () => {
  it.each<[string, (b: ItemBundle, s: any) => boolean]>([
    ["a command's session reports no model", (b, s) => !agentic(b, s) && s.model != null],
    ["a command's session reports no harness", (b, s) => !agentic(b, s) && s.harness != null],
    ["a command's session reports no usage", (b, s) => !agentic(b, s) && (s.tokens_in != null || s.tokens_out != null || s.cost_usd != null)],
    ["a command's session wrote no summary", (b, s) => !agentic(b, s) && s.session_summary_ref != null],
    ["a command's log holds its output only: no tool call, no agent line", (b, s) => !agentic(b, s) && logOf(b, s).some((l) => l.src !== "stdout" || l.agent)],
    ["an agent's session reports the claude harness", (b, s) => agentic(b, s) && s.harness !== "claude"],
    ["a test or lint run has the command it ran", (_b, s) => ["unit_tests", "lint", "typecheck"].includes(s.hook_point.split(".").at(-1)) && !s.command],
    ["a model is a current one", (b, s) => THE_ONES_ON_THE_BOARD.has(b.item.id) && s.model != null && !/^claude-(sonnet|opus|haiku)-5/.test(s.model)],
  ])("%s", (_rule, rule) => {
    expect(offenders(SAMPLE, rule)).toEqual([]);
  });
});

/** Fresh each time: a door changes the scenario it is given. */
const live = () => {
  const S = buildScenario("default");
  return { S, T: transitions(S) };
};

describe("sessions the doors make", () => {
  it("a command's task gets a command's session, which exits with no usage", () => {
    const { S, T } = live();
    const id = S.ng.running;
    const sid = T.createSession(id, "verification", "verification.test.unit_tests").body.session_id;
    T.sessionExited(id, sid, null);
    const b = S.bundles[id];
    const s = b.sessions.find((x) => x.id === sid)!;
    expect(s).toMatchObject({ status: "done", model: null, harness: null, command: "uv run pytest -q", tokens_in: null });
    expect(logOf(b, s).map((l) => l.src)).toEqual(logOf(b, s).map(() => "stdout"));
  });

  it("an agent's task gets the claude harness, and its model once it reports usage", () => {
    const { S, T } = live();
    const id = S.ng.running;
    const sid = T.createSession(id, "work_brief", "work_brief.write.work_brief").body.session_id;
    const s = () => S.bundles[id].sessions.find((x) => x.id === sid)!;
    expect(s()).toMatchObject({ harness: "claude", model: null });
    T.sessionRunning(id, sid);
    T.sessionExited(id, sid, { model: MODEL, tokens_in: 30_000, tokens_out: 4_000, cost_usd: 0.1 });
    expect(s()).toMatchObject({ harness: "claude", model: MODEL, tokens_in: 30_000 });
  });

  it("an escalation turn is an agent's, though the last session was a forge call's", () => {
    const { S, T } = live();
    const id = S.ng.failed;
    const b = S.bundles[id];
    expect(agentic(b, b.sessions.at(-1))).toBe(false);
    expect(T.escalate(id, "Why did the forge refuse?").status).toBe(200);
    const turn = b.sessions.at(-1)!;
    expect(turn).toMatchObject({ hook_point: "escalation", harness: "claude", model: MODEL, command: null });
    expect(logOf(b, turn).some((l) => l.src === "agent")).toBe(true);
  });

  it("a steered attempt of a command's task stays a command's", () => {
    const { S, T } = live();
    const id = S.ng.paused;
    const b = S.bundles[id];
    // The paused item's verification node, its last task the lint run.
    b.sessions = b.sessions.filter((s) => !["code_review", "typecheck", "security_scan"].includes(s.hook_point.split(".").at(-1)));
    expect(b.sessions.at(-1)!.hook_point).toBe("verification.checks.lint");
    expect(T.resume(id, "Look at the unused imports.").status).toBe(200);
    expect(b.sessions.at(-1)).toMatchObject({ hook_point: "verification.checks.lint", status: "pending", harness: null, model: null, command: "uv run ruff check ." });
  });
});

describe("taskKind", () => {
  const chain = (nodes: unknown[]) => JSON.stringify({ chain: { nodes } });
  it.each([
    ["a task under its step", chain([{ id: "verification", steps: [{ id: "tests", tasks: [{ id: "run", kind: "builtin" }] }] }]), "verification.tests.run", "builtin"],
    ["a node's own tasks sit under step main", chain([{ id: "implementation", steps: null, tasks: [{ id: "implement", kind: "agent" }] }]), "implementation.main.implement", "agent"],
    ["a task the chain does not name", chain([{ id: "spec", steps: [] }]), "spec.write.spec", null],
    ["no frozen chain", null, "spec.write.spec", null],
  ] as const)("%s", (_id, frozen, hook, kind) => {
    expect(taskKind(frozen, hook)).toBe(kind);
  });

  it.each([
    ["an escalation turn", null, "escalation", false, true],
    ["a task the chain names as a forge call", chain([{ id: "mr", steps: [{ id: "open", tasks: [{ id: "open_draft", kind: "forge" }] }] }]), "mr.open.open_draft", true, false],
    ["a task the chain does not name, the row reporting a model", null, "x.y.z", true, true],
    ["a task the chain does not name, the row reporting none", null, "x.y.z", false, false],
  ] as const)("isAgentSession: %s", (_id, frozen, hook, hasModel, agent) => {
    expect(isAgentSession(frozen, hook, hasModel)).toBe(agent);
  });
});
