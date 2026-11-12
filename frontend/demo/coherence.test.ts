// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { buildScenario, searchFor, withScopes, type ItemBundle, type Scenario } from "../e2e/contract/mocks/fixtures";
import { FRESH_TITLES, motion } from "../e2e/contract/mocks/motion";
import { NG_REPOS } from "../e2e/contract/mocks/ngBoard";
import { CONTENT, docBody, FRESH_CONTENT, isDocsPath, isLate, mergeDocs, mrOf } from "../e2e/contract/mocks/ngContent";
import { frozenOf } from "../e2e/contract/mocks/ngChain";
import { AUTO_ESCALATE_MESSAGE, buildNgItem, MR_CLOSED_REASON, NG_NOW, renderNote } from "../e2e/contract/mocks/ngItems";
import { agentUsage, openWait } from "../e2e/contract/mocks/nodeRecipe";
import { isAgentSession, MODEL, nodeTasks, PRICE, RUNS, runOutput, taskKind, taskRun } from "../e2e/contract/mocks/taskKinds";
import { transitions } from "../e2e/contract/mocks/transitions";
import { mockServer, type Answer } from "../e2e/contract/mocks/mockApi";
import { demoOptions } from "./scenario";

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
/** A task's own time cap in minutes, as its frozen chain gives it (`policy.time_cap_minutes`), or null. */
const capOf = (b: ItemBundle, hook: string): number | null => {
  const [node, step, task] = hook.split(".");
  const n = JSON.parse(b.item.materialized_chain ?? '{"chain":{"nodes":[]}}').chain.nodes.find((x: any) => x.id === node);
  const t = (step === "main" ? n?.tasks : n?.steps?.find((x: any) => x.id === step)?.tasks)?.find((x: any) => x.id === task);
  return t?.policy?.time_cap_minutes ?? null;
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
/** The board as the demo's live motion walks it (motion.ts), its clock going on with each 8 s tick: the gates waiting when
 *  it starts approved, then each gate the walk asks approved once its reviewer has said it is ready, until the second item
 *  the walk files (the first fails a round of tests, the second does not) has finished. Built once: it takes a few hundred ticks. */
let walk: Scenario | null = null;
/** Where the walk's clock stood when it stopped. */
let walkedTo = NG_NOW;
const walked = (): Scenario => {
  if (walk) return walk;
  vi.useFakeTimers();
  try {
    const S = buildScenario("default");
    let at = Date.parse(NG_NOW);
    const T = transitions(S, { now: () => new Date(at).toISOString() });
    for (const id of S.ngBoard) if (S.bundles[id].item.pending_gate) T.approve(id, S.bundles[id].item.pending_gate);
    const m = motion(S, T, { random: () => 0 });
    const done = () => S.ngBoard.some((id) => S.bundles[id].item.title === FRESH_TITLES[1] && S.bundles[id].item.status === "completed");
    for (let k = 0; k < 400 && !done(); k++) {
      at += 8_000;
      vi.advanceTimersByTime(8_000);
      for (const id of S.ngBoard) {
        const b = S.bundles[id];
        if (b.item.pending_gate && b.events.at(-1)?.type === "gate_auto_review_skipped") T.approve(id, b.item.pending_gate);
      }
    }
    m.stop();
    if (!done()) throw new Error("the walk did not finish the second item it filed");
    walkedTo = new Date(at).toISOString();
    walk = S;
    return S;
  } finally {
    vi.useRealTimers();
  }
};
const SCENARIOS: [string, () => Scenario][] = [
  ["the default sample", () => buildScenario("default")],
  ["the long variant", () => buildScenario("long")],
  ["the many variant", () => buildScenario("many")],
  ["test scopes", () => seeded(withScopes)],
  ["the live walk", walked],
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
    // `usage.py`: an exit reports every kind of token, and its cost is theirs at its model's prices. The older samples,
    // with no frozen chain, carry made-up figures and no cache split.
    ["an agent's cost is what its tokens cost, to a millionth of a dollar", (b, s) => !!b.item.materialized_chain && s.model === MODEL && s.cost_usd != null
      && !(Math.abs(Object.entries(PRICE).reduce((a, [k, usd]) => a + (s[k] ?? NaN) * usd / 1e6, 0) - s.cost_usd) < 1e-6)],
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
    // `walk` writes `chain_loaded` on every walk from the first node.
    ["an item that started a node loaded its chain first",
      (b) => {
        const first = b.events.findIndex((e) => e.type === "node_started");
        const loaded = b.events.filter((e) => e.type === "chain_loaded").length;
        // The demo models an item's first walk: once, before its first node (the server also writes it on a retry at the first node).
        return first >= 0 && (!b.events.slice(0, first).some((e) => e.type === "chain_loaded") || loaded > 1) && `${loaded} chain_loaded, first node at event ${first}`;
      }],
    // `store.budget`'s rollup sums every kind of token (`usage.KINDS`) of a node's sessions, and of the item's.
    ["a node's and the item's token rollups are the sum of their sessions, kind by kind",
      (b) => {
        const u = b.item.usage;
        if (!u) return false;
        const kinds = ["tokens_in", "tokens_cache_write", "tokens_cache_read", "tokens_out"];
        const sum = (ss: any[], k: string) => ss.reduce((a, x) => a + (x[k] ?? 0), 0);
        return [
          ...u.by_node.flatMap((r: any) => kinds.filter((k) => (r[k] ?? 0) !== sum(b.sessions.filter((x) => x.node_id === r.node), k)).map((k) => `${r.node}.${k}`)),
          ...kinds.filter((k) => (u.total[k] ?? 0) !== sum(b.sessions, k)).map((k) => `total.${k}`),
          // `budget.usage_rollup` says whether the cache was counted apart: the demo's sessions all report it.
          ...[...u.by_node, u.total].filter((r: any) => r.split_complete !== true).map((r: any) => `${r.node ?? "total"}.split_complete`),
        ];
      }],
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
    // `dispatch.over_budget` runs before an agent task launches: a spend cap stops the item on the node whose agent task
    // it held back, which has no session yet; a node whose every task ran would have completed.
    ["a spend cap stops it on a node with an agent task still to launch", (b) => b.item.stop?.kind === "budget"
      && !(nodeTasks(b.item.materialized_chain, b.item.stop.node) ?? []).some((t) => t.kind === "agent" && !b.sessions.some((s) => s.hook_point === t.path)) && b.item.stop.node],
    // A run as long as its task's time cap would have been stopped by it (`time_cap_reached`, capped out).
    ["an agent's finished session ran shorter than its task's time cap", (b) => b.sessions.filter((s) => ["done", "done_with_concerns"].includes(s.status) && capOf(b, s.hook_point) && !(s.wall_ms < capOf(b, s.hook_point)! * 60_000)).map((s) => `${s.hook_point}: ${s.wall_ms}`)],
    // `config/library.yaml`: every successful implementer run on record finished inside 96 minutes.
    ["an implementer's finished run is no longer than the longest successful one on record", (b) => b.sessions.filter((s) => s.hook_point === "implementation.main.implement" && ["done", "done_with_concerns"].includes(s.status) && s.wall_ms > 96 * 60_000).map((s) => `${s.hook_point}: ${s.wall_ms}`)],
  ])("%s", (_rule, wrong) => {
    const found = chained(S).map((b) => [b.item.title, wrong(b)] as const).filter(([, w]) => (Array.isArray(w) ? w.length > 0 : !!w));
    expect(found).toEqual([]);
  });

  // `events.append`: the column is the node the payload names (`node_id`, else `node`); a gate's own events name theirs
  // as `gate` (`store.gates` passes it).
  it("an event whose payload names a node carries it in its node_id column", () => {
    const named = (e: any) => [e.payload.node_id, e.payload.node, e.type.startsWith("gate_") ? e.payload.gate : null].find((x) => typeof x === "string");
    const wrong = Object.values(S.bundles).flatMap((b) => b.events.filter((e) => named(e) !== undefined && e.node_id !== named(e)).map((e) => `${b.item.title}: ${e.type}@${e.node_id}`));
    expect(wrong).toEqual([]);
  });

  it("no two board items share a title", () => {
    const titles = [...S.ngBoard, ...S.ngArchived].map((id) => S.bundles[id].item.title);
    expect(titles.filter((x, k) => titles.indexOf(x) !== k)).toEqual([]);
  });
});

/** The gate whose approval indexes each document, and the document's kind (the gate's `artifact`, `config/chains/default.yaml`):
 *  the spec at `spec_approval`, the plan at `plan_approval`, the chain revision at `chain_revision_approval`, the work brief
 *  at `local_review`, the review brief at `final_review`. A gate the item's chain lacks indexes nothing (`docs_only` has no
 *  `local_review`), and a chain revision Kraft passed itself because it changed nothing is never indexed
 *  (`pass_unchanged_revision` approves it `by: kraft`, not through `apply_approval`). Written out, not read off the mock. */
const GATED: Record<string, string> = { spec_approval: "spec", plan_approval: "plan", chain_revision_approval: "chain_revision", local_review: "work_brief", final_review: "review_brief" };
/** The approval that indexed the gate's document: the last a person or a reviewer gave it. */
const approvalOf = (b: ItemBundle, gate: string) => [...b.events].reverse().find((e) => e.type === "gate_approved" && e.payload.gate === gate && ["human", "agent"].includes(String(e.payload.by)));
const indexedGates = (b: ItemBundle) => Object.keys(GATED).filter((g) => b.item.chain_definition.nodes.some((n: any) => n.id === g) && approvalOf(b, g));
const completed = (b: ItemBundle, node: string) => b.events.some((e) => e.type === "node_completed" && e.payload.node_id === node);
const ENDED_STATUSES = ["completed", "abandoned"];
type Row = [path: string, insertions: number, deletions: number, ...flags: string[]];
const late = (f: Row) => f.slice(3).includes("late");
/** Whether a repair of verification's fix loop has finished: the one task that commits inside verification. */
const repaired = (b: ItemBundle) => b.sessions.some((s) => s.hook_point === "verification.fix_loop.main.repair" && s.status === "done");
/** What the item has committed: a task's file once the next task started (Kraft commits after every task), all the
 *  implementation's files once it completed, and verification's round's once a repair of its fix loop finished. */
const committed = (b: ItemBundle): Row[] => {
  const files = b.content!.files as Row[];
  if (completed(b, "implementation")) return files.filter((f) => !late(f) || repaired(b));
  const started = Math.max(0, ...b.events.filter((e) => e.type === "plan_progress" && e.payload.node_id === "implementation").map((e) => Number(e.payload.task)));
  return files.filter((f) => !late(f)).slice(0, Math.max(0, started - 1));
};
/** Each file's patch in `diff`: its path, its header lines, and its `@@` counts against the lines it shows. An empty
 *  row is a context line, as the review page reads one (`patch.ts`); its `---` and `+++` lines name its own path on
 *  each side, or `/dev/null` for the side a new or deleted file lacks (`named`). */
const patches = (diff: string) => diff.split(/^(?=diff --git )/m).filter(Boolean).map((p) => {
  const [head, ...rest] = p.split(/^(?=@@ )/m);
  const body = rest.join("").replace(/\n$/, "").split("\n");
  const [, oldN, newN] = /^@@ -\d+,(\d+) \+\d+,(\d+) @@/.exec(body[0])!.map(Number);
  const rows = body.slice(1);
  const count = (marks: string) => rows.filter((r) => marks.includes(r === "" ? " " : r[0])).length;
  const path = /^diff --git a\/(\S+)/.exec(head)![1];
  const side = (mark: string) => /^(?:---|\+\+\+) (\S+)$/m.exec(head.split("\n").filter((l) => l.startsWith(`${mark} `)).join("\n"))?.[1];
  const named = [side("---"), side("+++")].every((x, k) => x === "/dev/null" || x === `${"ab"[k]}/${path}`);
  return { path, head, counted: oldN === count(" -") && newN === count(" +"), named, oldN, newN };
});
/** An MR's address as its repo's forge writes it: GitHub's pull, or a merge request on the GitLab account's host. */
const MR_URL = /https:\/\/\S+\/(?:pull|-\/merge_requests)\/\d+/g;
const forgeUrl = (repo: string, n: number) => {
  const name = repo.split("/").pop()!;
  return NG_REPOS.find((r) => r.id === name)!.forge === "gitlab" ? `https://code.corp.example/gitlab/acme/${name}/-/merge_requests/${n}` : `https://github.com/acme/${name}/pull/${n}`;
};
/** Each kind's own section: what tells a spec from a plan or a summary. */
const SECTION: Record<string, string> = { specs: "\n## Problem\n", plans: "\n### Task 1: ", chain_revisions: "\n## Nodes\n", work_briefs: "\n## What changed\n", review_briefs: "\n## Read first\n", sessions: "\n## What this session did\n" };
/** A docs path is the mock's own (ngContent.ts `isDocsPath`): its documents' wording and these rules read one definition,
 *  which a table of paths written out below holds. */
const docsOnly = (files: Row[]) => files.every(([p]) => isDocsPath(p));
const isTestPath = (p: string) => /(^|\/)tests?\//.test(p) || /\.test\.[jt]sx?$/.test(p);
/** What is wrong with a brief's or a merge request's list of files, written after the implementation, against `shows`,
 *  the files its diff shows: each row `- \`path\` (+i −d)` one of them with its counts, all of them listed; a review
 *  brief's "Read first" the largest change and its "N files" the rows it lists. Null when nothing is. */
const briefProblem = (text: string, kind: string, shows: Row[]): string | null => {
  const listed = [...text.matchAll(/^- `([^`]+)` \(\+(\d+) −(\d+)\)$/gm)].map((m) => `${m[1]} ${m[2]} ${m[3]}`).sort();
  if (JSON.stringify(listed) !== JSON.stringify(shows.map(([p, i, d]) => `${p} ${i} ${d}`).sort())) return `lists ${listed.join(", ")}`;
  if (kind !== "review_brief") return null;
  const top = Math.max(...shows.map(([, i, d]) => i + d));
  const first = /^`([^`]+)`, the largest change \(\+(\d+) −(\d+)\)\.$/m.exec(text);
  if (!first || Number(first[2]) + Number(first[3]) !== top || !shows.some(([p, i, d]) => p === first[1] && i === Number(first[2]) && d === Number(first[3]))) return `reads ${first?.[1]} first`;
  return /^- Nothing outside the (\d+) files below changed\.$/m.exec(text)?.[1] === String(listed.length) ? null : "miscounts its files";
};

describe.each<[string, () => Scenario, boolean]>([["the default sample", () => buildScenario("default"), true], ["the live walk", walked, false]])("each board item's own content in %s", (_scenario, build, sample) => {
  const S = build();
  const board = [...S.ngBoard, ...S.ngArchived].map((id) => S.bundles[id]);
  const serve = mockServer(S, demoOptions);
  const call = async (path: string) => {
    const a = (await serve({ method: "GET", url: `http://demo/api${path}`, json: () => null })) as Answer;
    return { status: a.status, body: a.body ? JSON.parse(a.body) : null };
  };
  /** What the item header sums: every file the diff names, landed or in flight, once per path. */
  const served = async (b: ItemBundle): Promise<{ path: string; insertions: number; deletions: number }[]> => {
    const d = await call(`/work-items/${b.item.id}/diff`);
    expect(d.status, b.item.title).toBe(200);
    return [...d.body.landed.files, ...d.body.files];
  };
  const withTree = board.filter((b) => b.item.worktree_exists !== false && b.events.some((e) => e.type === "node_started"));
  const doneAgent = (s: any) => s.session_summary_ref && ["done", "done_with_concerns"].includes(s.status);
  const itemOf = (r: any) => r.links.find((l: any) => l.work_item_id)?.work_item_id;

  it("every board item has a brief, its own, and no two share one", () => {
    const briefs = board.map((b) => b.item.description);
    expect(board.filter((b) => !b.item.description || b.item.description !== b.content?.description).map((b) => b.item.title)).toEqual([]);
    expect(briefs.filter((x, k) => briefs.indexOf(x) !== k)).toEqual([]);
  });
  // The strict rule: every entry of the content table is written for its own item, so no list may repeat, though two
  // rows of one kind in one repo might in principle change the same files.
  it("no two board items that have changed something share the list of files they changed", async () => {
    const lists = (await Promise.all(withTree.map(served))).map((fs) => fs.map((f) => f.path).sort().join(" ")).filter(Boolean);
    expect(lists.length).toBeGreaterThan(0);
    expect(lists.filter((x, k) => lists.indexOf(x) !== k)).toEqual([]);
  });
  it("an item that has started no plan task has changed nothing", async () => {
    const before = board.filter((b) => !completed(b, "implementation") && !b.events.some((e) => e.type === "plan_progress"));
    expect(before.length).toBeGreaterThan(0);
    const wrong = await Promise.all(before.filter((b) => b.item.worktree_exists !== false).map(async (b) => [b.item.title, (await served(b)).length] as const));
    expect(wrong.filter(([, n]) => n > 0)).toEqual([]);
  });
  it("an item stopped inside its implementation reports its plan's own tasks, and has committed the files of those it finished", async () => {
    const inside = board.filter((b) => !completed(b, "implementation") && b.events.some((e) => e.type === "plan_progress"));
    if (sample) expect(inside.length).toBeGreaterThan(0);
    const wrong = await Promise.all(inside.map(async (b) => {
      const plan = (await call(`/work-items/${b.item.id}/artifacts/plan`)).body.content as string;
      const tasks = [...plan.matchAll(/^### Task \d+: (.*)$/gm)].map((m) => m[1]);
      const told = b.events.filter((e) => e.type === "plan_progress").map((e) => [e.payload.title, e.payload.total]);
      const files = (await served(b)).map((f) => f.path);
      const ok = JSON.stringify(told) === JSON.stringify(tasks.slice(0, told.length).map((t) => [t, tasks.length])) && files.length > 0 && JSON.stringify(files) === JSON.stringify(committed(b).map(([p]) => p).sort());
      return ok ? null : b.item.title;
    }));
    expect(wrong.filter(Boolean)).toEqual([]);
  });
  // Only verification's repair commits inside verification: a row whose round never repaired has no file it added.
  it("a board row lists a file verification's round added only where a repair of its ran", () => {
    const rows = board.filter((b) => (b.content!.files as Row[]).some(late));
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.filter((b) => !b.sessions.some((s) => s.hook_point === "verification.fix_loop.main.repair")).map((b) => b.item.bead_id ?? b.item.title)).toEqual([]);
  });
  // A round of tests fails on the test of the file that round goes on to add (its `late` file), in the finding the round
  // measured and in its session's log; a page's check is its doctest. Written out, not read off the mock.
  it("a red round of tests fails on the item's own late file's test, in its finding and its log", () => {
    const want: Record<string, string> = {
      "kraft-a3f0": "FAILED tests/test_limiter.py::test_limiter - AssertionError",
      "kraft-91bc": "FAILED src/test/board.test.ts::board - AssertionError",
      "kraft-2c77": "FAILED tests/lint/test_report.py::test_report - AssertionError",
      "kraft-88ca": "FAILED tests/harness/test_aliases.py::test_aliases - AssertionError",
      "kraft-7c40": "FAILED tests/index/test_service.py::test_service - AssertionError",
      "kraft-b2e7": "FAILED tests/console/test_app.py::test_app - AssertionError",
      "kraft-6a2f": "FAILED README.md::README.md",
      ...(!sample && { [FRESH_TITLES[0]]: "FAILED tests/forge/test_run.py::test_run - AssertionError" }),
    };
    const got = Object.fromEntries(board.flatMap((b) => {
      const said = [
        ...b.events.filter((e) => e.type === "findings_measured").flatMap((e) => (e.payload.findings as any[]).map((f) => String(f.message))),
        ...b.sessions.filter((s) => s.hook_point === "verification.tests.test_changed_scopes" && s.status === "failed").flatMap((s) => logOf(b, s).map((l) => l.text)),
      ].flatMap((x) => x.split("\n")).map((x) => /FAILED \S+(?: - AssertionError)?/.exec(x)?.[0]).filter(Boolean);
      return said.length ? [[b.item.bead_id ?? b.item.title, [...new Set(said)].join(" | ")]] : [];
    }));
    expect(got).toEqual(want);
  });
  it("a docs item changes docs only, in its table and in the diff it serves", async () => {
    const docs = board.filter((b) => b.item.repo.endsWith("/kraft-docs") || b.item.chain_template === "docs_only");
    expect(docs.length).toBeGreaterThan(0);
    expect(docs.flatMap((b) => b.content!.files.map(([p]) => p).filter((p) => !isDocsPath(p)))).toEqual([]);
    const servedPaths = (await Promise.all(docs.filter((b) => withTree.includes(b)).map(served))).flat().map((f) => f.path);
    expect(servedPaths.length).toBeGreaterThan(0);
    expect(servedPaths.filter((p) => !isDocsPath(p))).toEqual([]);
  });
  it("its header's stat, comparison and diff are its committed files', path-sorted, each patch counted and marked new or deleted as it is", async () => {
    const wrong = await Promise.all(withTree.map(async (b) => {
      const want = committed(b);
      const d = (await call(`/work-items/${b.item.id}/diff`)).body;
      const got = [...d.landed.files, ...d.files];
      const cmp = (await call(`/work-items/${b.item.id}/compare`)).body;
      const paths = want.map(([p]) => p).sort();
      const status = (p: string) => { const f = want.find(([x]) => x === p)!; return f.slice(3).includes("new") ? "new" : f.slice(3).includes("deleted") ? "deleted" : null; };
      const marked = (x: ReturnType<typeof patches>[number]) => {
        const s = status(x.path);
        return s === "new" ? x.head.includes("\nnew file mode 100644\n") && x.head.includes("\n--- /dev/null\n") && x.oldN === 0
          : s === "deleted" ? x.head.includes("\ndeleted file mode 100644\n") && x.head.includes("\n+++ /dev/null\n") && x.newN === 0
          : !/new file mode|deleted file mode|\/dev\/null/.test(x.head);
      };
      const problems = [
        JSON.stringify(got.map((f: any) => [f.path, f.insertions, f.deletions])) !== JSON.stringify([...want].sort((x, y) => (x[0] < y[0] ? -1 : 1)).map(([p, i, dl]) => [p, i, dl])) && "stat",
        JSON.stringify(cmp.files.map((f: any) => [f.path, f.insertions, f.deletions])) !== JSON.stringify(got.map((f: any) => [f.path, f.insertions, f.deletions])) && "compare",
        // Each file is touched by the node that committed it: verification's round's by verification, the rest by implementation.
        cmp.files.some((f: any) => JSON.stringify(f.touched_by) !== JSON.stringify([late(want.find(([x]) => x === f.path)!) ? "verification" : "implementation"])) && "touched_by",
        ...[d.landed.diff + d.diff, cmp.diff].map((text: string, k) => {
          const ps = patches(text);
          return (JSON.stringify(ps.map((x) => x.path)) !== JSON.stringify(paths) || !ps.every((x) => x.counted && x.named && marked(x))) && `diff ${k}`;
        }),
      ].filter(Boolean);
      return problems.length ? `${b.item.title}: ${problems.join(", ")}` : null;
    }));
    expect(wrong.filter(Boolean)).toEqual([]);
  });
  // A review thread sits on lines the item's own diff shows, and quotes them as the diff has them, each row's mark included.
  it("each review thread is on a file of its item's diff, and quotes the new-side rows it spans", async () => {
    const threaded = board.filter((b) => b.threads?.length);
    if (sample) expect(threaded.length).toBeGreaterThan(0);
    const wrong: string[] = [];
    for (const b of threaded) {
      const diff = (await call(`/work-items/${b.item.id}/diff`)).body;
      const text = diff.landed.diff + diff.diff;
      for (const th of b.threads ?? []) {
        const p = text.split(/^(?=diff --git )/m).find((x: string) => x.startsWith(`diff --git a/${th.file_path} `));
        const lines = new Map<number, string>();
        if (p) {
          const [, start] = /^@@ -\d+,\d+ \+(\d+),\d+ @@$/m.exec(p)!.map(Number);
          let n = start;
          for (const row of p.split(/^@@ .*$/m)[1].replace(/^\n/, "").replace(/\n$/, "").split("\n")) if (row[0] !== "-") lines.set(n++, row);
        }
        const span = Array.from({ length: th.end_line - th.start_line + 1 }, (_, k) => lines.get(th.start_line + k));
        // An item with a hunk of its own quotes it on every thread; the sample's local-review thread quotes nothing.
        if (!p || span.some((r) => r === undefined) || (b.content?.hunks && th.quote == null) || (th.quote != null && th.quote !== span.join("\n"))) wrong.push(`${b.item.title}: ${th.id}`);
        // What a comment names in backticks is code on the lines it sits on (a thread about `lookup` quotes `def lookup`).
        for (const c of th.comments) for (const [, name] of c.body.matchAll(/`([^`]+)`/g)) if (!th.quote?.includes(name)) wrong.push(`${b.item.title}: ${th.id} names ${name}`);
      }
    }
    expect(wrong).toEqual([]);
  });
  // `submit_review`'s note (`store.render_note`): the review's summary, then each thread open as it was submitted, where it
  // sits, the lines it quotes and what was said; a thread resolved by then is left out.
  it("a review's rejection note is its summary and the threads open when it was sent, each where it sits with its quote", () => {
    const threaded = board.filter((b) => b.threads?.length && b.reviews?.length);
    if (sample) expect(threaded.length).toBeGreaterThan(0);
    const wrong = threaded.flatMap((b) => (b.reviews ?? []).flatMap((r: any) => {
      const note = String(b.events.find((e) => e.type === "gate_rejected" && e.created_at === r.submitted_at)?.payload.note);
      const open = (b.threads as any[]).filter((th) => th.created_at <= r.submitted_at && !(th.resolved_at && th.resolved_at <= r.submitted_at));
      const block = (th: any) => [`[${th.id}] ${th.file_path}:+${th.start_line}${th.end_line === th.start_line ? "" : ` to +${th.end_line}`}${th.label ? ` (${th.label})` : ""}`, ...(th.quote ? th.quote.split("\n") : []).map((l: string) => `    | ${l}`), th.comments[0].body].join("\n");
      // `store.render_note`: the summary, the fixed head line, then the blocks, each part a blank line from the next.
      const head = 'Review threads to address (reply to each with `kraft item reply <thread-id> --claim fixed|answered --body "..."`):';
      // A request for changes counts what its note lists: "Three things" over three threads.
      const counted = r.outcome !== "request_changes" || r.summary.startsWith(`${["One", "Two", "Three", "Four", "Five"][open.length - 1]} thing`);
      const ok = open.length > 0 && counted && note === [r.summary, head, open.map(block).join("\n\n")].join("\n\n") && (b.threads as any[]).filter((th) => !open.includes(th)).every((th) => !note.includes(`[${th.id}]`));
      return ok ? [] : [`${b.item.title}: ${r.id}`];
    }));
    expect(wrong).toEqual([]);
  });
  it("a rejection's note leaves out a thread resolved by then, and is the summary alone when none is open", () => {
    const th = (id: string, state: string, at: number) => ({ id, state, file_path: "a/b.py", start_line: at, end_line: at, label: "nit", quote: "+x", comments: [{ body: `say ${id}` }] });
    const note = renderNote([th("open1", "open", 3), th("done1", "resolved", 5)], "S.");
    expect(note).toContain("[open1] a/b.py:+3 (nit)");
    expect(note).not.toContain("done1");
    expect(renderNote([th("done1", "resolved", 5)], "S.")).toBe("S.");
  });
  // A test imports the module of its name where the item changes one, from where that module is; the page a docs item is
  // about (its first) says what the item's brief says, and any other page does not, but names the item; a manifest is its
  // repo's own package.
  it("a file's patch reads as its kind: a test's asserts and imports, a page's words, a manifest's name, and only the VS Code repo's code imports vscode", async () => {
    const wrong: string[] = [];
    const stem = (p: string) => p.replace(/(\.test)?\.[^./]+$/, "");
    for (const b of withTree) for (const p of (await call(`/work-items/${b.item.id}/diff`)).body.landed.diff.split(/^(?=diff --git )/m).filter(Boolean)) {
      const path = /^diff --git a\/(\S+)/.exec(p)![1];
      const paths = (b.content!.files as Row[]).map(([x]) => x);
      const principal = docsOnly(b.content!.files as Row[]) && paths.find((x) => x.endsWith(".md")) === path;
      const brief = p.includes(b.item.description.split(". ")[0]);
      const module = b.content!.files.map(([x]) => x).find((x) => !isTestPath(x) && x !== path && stem(x).split("/").pop() === stem(path).split("/").pop());
      const imported = /^[ +]import \{ \w+ \} from "(\.[^"]+)";$/m.exec(p)?.[1];
      const at = path.split("/").slice(0, -1);
      for (const s of imported?.split("/") ?? []) if (s === "..") at.pop(); else if (s !== ".") at.push(s);
      if ((isTestPath(path) && !/^\+.*\b(assert|expect)\b/m.test(p)) || (p.includes(`from "vscode"`) && !b.item.repo.endsWith("/kraft-vscode"))
        || (isTestPath(path) && imported && module && at.join("/") !== stem(module))
        || (path.endsWith(".md") && (principal ? !brief : brief || !p.includes(b.item.title)))
        || (path.endsWith("package.json") && !p.includes(`"name": "${b.item.repo.split("/").pop()}",`))) wrong.push(`${b.item.title}: ${path}`);
    }
    expect(wrong).toEqual([]);
  });
  it("each task its implementer reports is its own plan's: task k of the plan's N, under the plan's own title", async () => {
    const reporting = board.filter((b) => b.events.some((e) => e.type === "plan_progress"));
    expect(reporting.length).toBeGreaterThan(0);
    const wrong = await Promise.all(reporting.map(async (b) => {
      const plan = (await call(`/work-items/${b.item.id}/artifacts/plan`)).body?.content ?? "";
      const tasks = [...plan.matchAll(/^### Task \d+: (.*)$/gm)].map((m: RegExpMatchArray) => m[1]);
      const bad = b.events.filter((e) => e.type === "plan_progress").some((e) => e.payload.total !== tasks.length || e.payload.title !== tasks[Number(e.payload.task) - 1]);
      // One task per file it changes (not those verification's round adds), then a check, last.
      const shape = tasks.length === (b.content!.files as Row[]).filter((f) => !late(f)).length + 1 && tasks.at(-1) === "Check it";
      return bad || !shape ? b.item.title : null;
    }));
    expect(wrong.filter(Boolean)).toEqual([]);
  });
  // A time cap stops the implementer mid-plan (capped_out); a spend cap or a moved checkout stops the item after the
  // task ran (`dispatch.over_budget` before the next launch, `inplace.check_task` after it), so its session is done.
  it("an implementer that ran to its end reported its whole plan, and one stopped short did not", () => {
    const wrong = board.filter((b) => b.events.some((e) => e.type === "plan_progress")).flatMap((b) => {
      const s = b.sessions.filter((x) => x.hook_point === "implementation.main.implement").at(-1)!;
      const told = b.events.filter((e) => e.type === "plan_progress");
      const all = Math.max(...told.map((e) => Number(e.payload.task))) === told[0].payload.total;
      return (s.status === "done") !== all ? [`${b.item.title}: ${s.status}, ${told.length} of ${told[0].payload.total}`] : [];
    });
    expect(wrong).toEqual([]);
  });
  // A chain with no plan node (docs_only) has no plan: `report_progress` answers 400, so its implementer reports no task
  // and names no commit after one.
  it("an item whose chain has no plan reports no plan task, and one that has a plan reports it", () => {
    const planless = board.filter((b) => !defIds(b).includes("plan"));
    expect(planless.length).toBeGreaterThan(0);
    expect(planless.filter((b) => b.events.some((e) => e.type === "plan_progress")).map((b) => b.item.title)).toEqual([]);
    expect(board.filter((b) => defIds(b).includes("plan") && completed(b, "implementation") && !b.events.some((e) => e.type === "plan_progress")).map((b) => b.item.title)).toEqual([]);
  });
  it("its commits are one per finished plan task (one named after it with no plan), then verification's fix, newest first", async () => {
    const wrong = await Promise.all(withTree.filter((b) => committed(b).length).map(async (b) => {
      const commits = (await call(`/work-items/${b.item.id}/diff`)).body.landed.commits as string[];
      const subjects = commits.map((c) => c.slice(8));
      const shas = commits.map((c) => c.slice(0, 7));
      if (shas.some((x, k) => shas.indexOf(x) !== k || !/^[0-9a-f]{7}$/.test(x))) return b.item.title;
      const own = committed(b).filter((f) => !late(f));
      const work = defIds(b).includes("plan") ? own.map(([p], k) => `Task ${k + 1}: Change ${p}`) : own.length ? [b.item.title] : [];
      const want = [...work, ...(committed(b).some(late) ? ["Fix what verification found"] : [])].reverse();
      return JSON.stringify(subjects) === JSON.stringify(want) ? null : b.item.title;
    }));
    expect(wrong.filter(Boolean)).toEqual([]);
  });
  it("it has a merge request once its draft's open task has run, its own on its repo's forge, the one its mr_opened named", () => {
    const wrong = board.flatMap((b) => {
      const opened = b.sessions.some((s) => s.hook_point === "draft_merge_request.open.open" && s.status === "done");
      const opening = b.events.filter((e) => e.type === "mr_opened").at(-1);
      const event = opening?.payload;
      const mr = b.item.mr_ref;
      const own = { number: b.content!.mr, url: forgeUrl(b.item.repo, b.content!.mr) };
      // `forge.run` writes `{number, url}`, its node in the column only.
      const shaped = !opening || (JSON.stringify(Object.keys(opening.payload).sort()) === '["number","url"]' && opening.node_id === "draft_merge_request");
      return opened !== !!mr || !shaped || (mr && (JSON.stringify(mr) !== JSON.stringify(own) || event?.number !== mr.number || event?.url !== mr.url)) ? [b.item.title] : [];
    });
    expect(wrong).toEqual([]);
    expect(board.some((b) => b.item.mr_ref?.url.includes("/-/merge_requests/"))).toBe(true);
  });
  it("a forge task's log names its own item's merge request, and no other", () => {
    const wrong = board.filter((b) => b.item.mr_ref).flatMap((b) => b.sessions.filter((s) => !agentic(b, s)).flatMap((s) =>
      logOf(b, s).filter((l) => (l.text.match(MR_URL) ?? []).some((u: string) => u !== b.item.mr_ref.url)).map((l) => `${b.item.title}: ${l.text}`)));
    expect(wrong).toEqual([]);
    expect(board.some((b) => b.item.mr_ref && b.sessions.some((s) => logOf(b, s).some((l) => l.text.includes(b.item.mr_ref.url))))).toBe(true);
  });
  it("a failed draft's log and its stop name the route its own repo's forge opens a merge request on", () => {
    const failed = board.filter((b) => b.sessions.some((s) => s.hook_point === "draft_merge_request.open.open" && s.status === "failed"));
    if (sample) expect(failed.map((b) => b.item.bead_id)).toContain("kraft-b108");
    const wrong = failed.filter((b) => {
      const route = forgeUrl(b.item.repo, 1).includes("/-/merge_requests/") ? "POST projects/:id/merge_requests: " : "POST repos/{owner}/{repo}/pulls: ";
      const s = b.sessions.find((x) => x.hook_point === "draft_merge_request.open.open" && x.status === "failed")!;
      const stop = b.events.find((e) => e.type === "work_item_needs_human" && e.payload.session_id === s.id);
      return !logOf(b, s).some((l) => l.text.includes(route)) || !String(stop?.payload.reason).includes(route);
    });
    expect(wrong.map((b) => b.item.title)).toEqual([]);
  });
  // A row stopped on its code review's question asks its own (CONTENT `question`), in its stop and in the blind finding
  // the round measured; the item page's sample question is about the sample's cache and is on no board item.
  it("an item stopped on a question asks its own, in its stop and its measured finding, and no board item asks the sample's", () => {
    const asking = board.filter((b) => b.content!.question);
    if (sample) expect(asking.map((b) => b.item.bead_id)).toEqual(["kraft-a3f0"]);
    const wrong = asking.filter((b) => {
      const q = b.content!.question!;
      const stop = b.events.filter((e) => e.type === "work_item_needs_human").at(-1);
      const found = b.events.filter((e) => e.type === "findings_measured").at(-1)?.payload.findings as any[] | undefined;
      return !String(stop?.payload.reason).includes(q) || !found?.some((f) => String(f.message).includes(q)) || b.item.needs_context_question !== q;
    });
    expect(wrong.map((b) => b.item.title)).toEqual([]);
    const generic = "The race needs a reindex API change the plan froze";
    expect(board.filter((b) => JSON.stringify([b.events, b.item]).includes(generic)).map((b) => b.item.title)).toEqual([]);
  });
  it("it was filed before anything it records, under the title its history opens with", () => {
    const wrong = board.filter((b) => b.events.length).filter((b) => {
      const created = b.events.find((e) => e.type === "work_item_created");
      return b.item.created_at > b.events[0].created_at || created?.payload.title !== b.item.title;
    });
    expect(wrong.map((b) => b.item.title)).toEqual([]);
  });
  it("no two documents share an id, on any item", () => {
    const ids = Object.values(S.docs).flat().map((d) => d.document_id);
    expect(ids.filter((x, k) => ids.indexOf(x) !== k)).toEqual([]);
  });
  it("its documents are the documents of the gates a person or reviewer approved and its finished agent sessions' summaries, in its own folder", () => {
    const wrong = board.flatMap((b) => {
      const id = b.item.id;
      const want = [
        ...indexedGates(b).map((g) => `items/${id}/docs/${GATED[g]}.md`),
        ...new Set(b.sessions.filter(doneAgent).map((s) => s.session_summary_ref)),
      ].sort();
      const got = (S.docs[id] ?? []).map((d) => d.path).sort();
      return JSON.stringify(got) !== JSON.stringify(want) || got.some((p) => !p.startsWith(`items/${id}/`)) ? [`${b.item.title}: ${got.length} of ${want.length}`] : [];
    });
    expect(wrong).toEqual([]);
  });
  // `documents_for_work_item`: a summary's row is its session's, indexed as the session exited; nothing is indexed after
  // the clock (the sample's, or where the walk's stood).
  it("each session summary's document is its session's, indexed when it exited, and none is indexed after the clock", () => {
    const clock = sample ? NG_NOW : walkedTo;
    const wrong = board.flatMap((b) => (S.docs[b.item.id] ?? []).flatMap((d) => {
      const s = b.sessions.find((x) => x.id === d.worker_session_id);
      const own = d.source_kind !== "session_summary" || (s && d.attempt === s.attempt && d.round === s.round && d.session_status === s.status && d.indexed_at === s.exited_at && d.node_id === s.node_id && d.hook_point === s.hook_point);
      return !own || d.indexed_at > clock ? [`${b.item.title}: ${d.path}`] : [];
    }));
    expect(wrong).toEqual([]);
  });
  // A row of the list carries every key a real one does (`tests/api/api_shapes.json`), each row, not only one of them;
  // the pending gate's the keys `_pending_gate_doc` writes, and the run keys (`attempt`, `round`, `session_status`) the
  // list's last pass sets on every row, its own included (`documents_for_work_item`).
  it("every row of its documents list carries the keys a real row carries", async () => {
    const shapes = JSON.parse(readFileSync(new URL("../../tests/api/api_shapes.json", import.meta.url), "utf-8"));
    const keys = Object.keys(shapes.routes["GET /api/work-items/{wid}/documents"].shape.documents[0]);
    const pending = ["attachment_kind", "attempt", "document_id", "hook_point", "indexed_at", "kind", "node_id", "path", "repo", "round", "session_status", "source_kind", "title", "worker_session_id"];
    const wrong: string[] = [];
    for (const b of board) for (const d of (await call(`/work-items/${b.item.id}/documents`)).body.documents) {
      const lacks = (String(d.document_id).startsWith("gate:") ? pending : keys).filter((k) => !(k in d));
      if (lacks.length) wrong.push(`${b.item.title}: ${d.path} lacks ${lacks.join(", ")}`);
    }
    expect(wrong).toEqual([]);
  });
  it("a gate document is indexed at its approval, with no node, task or session, and the list is newest first", async () => {
    const wrong = await Promise.all(board.map(async (b) => {
      const list = (await call(`/work-items/${b.item.id}/documents`)).body.documents;
      const gates = list.filter((d: any) => d.source_kind === "artifact" && !String(d.document_id).startsWith("gate:"));
      const timed = gates.every((d: any) => d.indexed_at === approvalOf(b, Object.keys(GATED).find((g) => `${GATED[g]}s` === d.kind)!)?.created_at && [d.node_id, d.hook_point, d.worker_session_id, d.session_work_item_id].every((x) => x === null));
      const sorted = list.every((d: any, k: number) => !k || list[k - 1].indexed_at >= d.indexed_at);
      return timed && sorted ? null : b.item.title;
    }));
    expect(wrong.filter(Boolean)).toEqual([]);
  });
  it("a chain revision that changed nothing has no document, and a docs item has no work brief", () => {
    const unchanged = board.filter((b) => b.events.some((e) => e.type === "chain_revision_unchanged"));
    const docsItems = board.filter((b) => b.item.chain_template === "docs_only" && completed(b, "work_brief"));
    expect([unchanged.length > 0, docsItems.length > 0]).toEqual([true, true]);
    expect([...unchanged.filter((b) => S.docs[b.item.id].some((d) => d.kind === "chain_revisions")), ...docsItems.filter((b) => S.docs[b.item.id].some((d) => d.kind === "work_briefs"))].map((b) => b.item.title)).toEqual([]);
  });
  it("a pending gate's document is listed first under its synthetic id, read live, and never found by search", async () => {
    const gated = board.filter((b) => b.item.pending_gate && b.item.gate_artifact);
    if (sample) expect(gated.length).toBeGreaterThan(0);
    const wrong = await Promise.all(gated.map(async (b) => {
      const id = `gate:${b.item.id}:${b.item.pending_gate}`;
      const [first, ...rest] = (await call(`/work-items/${b.item.id}/documents`)).body.documents;
      const doc = (await call(`/documents/${encodeURIComponent(id)}`)).body;
      const hits = (await call(`/search?q=${encodeURIComponent(b.item.title)}&limit=100`)).body.results;
      // Listed now, by the demo's clock (`clock.ts`), not the machine's.
      const ok = first.document_id === id && first.path === b.item.gate_artifact && [first.node_id, first.hook_point, first.worker_session_id].every((x) => x === null) && (!sample || first.indexed_at === NG_NOW)
        && !rest.some((d: any) => d.path === first.path) && !S.docs[b.item.id].some((d) => d.path === first.path)
        && doc.content.startsWith(`# ${first.title}\n`) && JSON.stringify([doc.metadata, doc.links, doc.source_created_at, "origin" in doc]) === JSON.stringify([{}, [], null, false])
        && !hits.some((r: any) => r.id === id || r.path === first.path);
      return ok ? null : b.item.title;
    }));
    expect(wrong.filter(Boolean)).toEqual([]);
  });
  // `_get_pending_gate_document`: a synthetic id names the gate pending now; another gate's, or a gate on an item with
  // none pending, is no document.
  it("a synthetic gate id that names no gate pending now is a 404", async () => {
    const gated = board.find((b) => b.item.pending_gate && b.item.gate_artifact);
    const idle = board.find((b) => !b.item.pending_gate && S.docs[b.item.id]?.length)!;
    const ids = [...(gated ? [`gate:${gated.item.id}:spec_approval`] : []), `gate:${idle.item.id}:final_review`];
    const answers = await Promise.all(ids.map(async (id) => { const a = await call(`/documents/${encodeURIComponent(id)}`); return [a.status, a.body.detail]; }));
    expect(answers).toEqual(ids.map(() => [404, "unknown document"]));
    if (gated) expect((await call(`/documents/${encodeURIComponent(`gate:${gated.item.id}:${gated.item.pending_gate}`)}`)).status).toBe(200);
  });
  /** Whether a document's text is about `b`: it names its title and says what its brief's first sentence says. */
  const about = (text: string, b: ItemBundle) => text.includes(b.item.title) && text.includes(b.item.description.split(". ")[0]);
  // An escalation reply the walk's agent wrote (`docText`) has its own words and no front matter; motion.test.ts holds it.
  it("each document opens to its own item, headed by its title, with its kind's own section, about it, as the indexer ingested it", async () => {
    const wrong: string[] = [];
    for (const b of board) for (const d of (S.docs[b.item.id] ?? []).filter((x) => S.docText?.[x.document_id] == null)) {
      const doc = (await call(`/documents/${d.document_id}`)).body;
      const summary = d.source_kind === "session_summary";
      const meta = summary ? ["hook_point", "node_id", "work_item_ids", "worker_session_id"] : ["hook_point", "node_id", "work_item_ids"];
      const item = { work_item_id: b.item.id, node_id: null, hook_point: null, worker_session_id: null, session_work_item_id: null };
      const links = summary ? [{ work_item_id: null, node_id: d.node_id, hook_point: d.hook_point, worker_session_id: d.worker_session_id, session_work_item_id: b.item.id }, item] : [item];
      const ok = doc.id === d.document_id && d.repo === b.item.repo && doc.metadata.work_item_ids.includes(b.item.id) && doc.content.startsWith(`# ${d.title}\n`) && doc.content.includes(SECTION[d.kind]) && about(doc.content, b)
        && doc.origin === "event_ingest" && doc.source_created_at === null && doc.source_updated_at === null
        && JSON.stringify(Object.keys(doc.metadata).sort()) === JSON.stringify(meta) && JSON.stringify(doc.links) === JSON.stringify(links)
        && (summary || (doc.metadata.node_id !== null && doc.metadata.hook_point !== null));
      if (!ok) wrong.push(`${b.item.title}: ${d.path}`);
    }
    expect(wrong).toEqual([]);
  });
  it("each kind a document of its item has is served by its kind, about it, from its own folder; the pending gate's too", async () => {
    const wrong: string[] = [];
    for (const b of board) {
      const kinds = new Set((S.docs[b.item.id] ?? []).filter((d) => d.source_kind === "artifact").map((d) => d.kind.replace(/s$/, "")));
      for (const kind of kinds) {
        const a = await call(`/work-items/${b.item.id}/artifacts/${kind}`);
        if (a.status !== 200 || a.body.path !== `items/${b.item.id}/docs/${kind}.md` || !a.body.content.startsWith(`# ${a.body.title}\n`) || !a.body.content.includes(SECTION[`${kind}s`]) || !about(a.body.content, b)) wrong.push(`${b.item.title}: ${kind}`);
      }
      if (b.item.pending_gate && b.item.gate_artifact) {
        const a = (await call(`/work-items/${b.item.id}/artifact`)).body;
        if (!a.path.startsWith(`items/${b.item.id}/`) || !about(a.content, b)) wrong.push(`${b.item.title}: the pending gate's`);
      }
    }
    expect(wrong).toEqual([]);
  });
  // A spec, plan and chain revision are written before anything changed: no counts, and the plan names no file
  // verification's round added; a work and a review brief are written after, with counts. A session that ran before
  // there was a plan (a spec's or plan's writer, a gate's reviewer) says nothing of what the plan names.
  it("what each document says fits when it was written", async () => {
    const wrong: string[] = [];
    let counts = 0;
    let briefs = 0;
    for (const b of board) {
      for (const kind of ["spec", "plan", "chain_revision", "work_brief", "review_brief"]) {
        const a = await call(`/work-items/${b.item.id}/artifacts/${kind}`);
        if (a.status !== 200) continue;
        const lateNamed = (b.content!.files as Row[]).filter(late).some(([p]) => a.body.content.includes(p));
        if (/\(\+\d+ −\d+\)/.test(a.body.content) !== kind.endsWith("brief") || (!kind.endsWith("brief") && lateNamed)) wrong.push(`${b.item.title}: ${kind}`);
      }
      // A brief or a merge request, written after the implementation, lists the files its diff shows: no file the round
      // added unless a repair landed it.
      // Each with the counts its diff gives it; a review brief says how many files it lists, and reads the largest first.
      for (const kind of ["work_brief", "review_brief", "mr_meta"]) {
        const a = await call(`/work-items/${b.item.id}/artifacts/${kind}`);
        if (a.status !== 200) continue;
        briefs++;
        const problem = briefProblem(a.body.content, kind, committed(b));
        if (problem) wrong.push(`${b.item.title}: ${kind} ${problem}`);
      }
      for (const d of (S.docs[b.item.id] ?? []).filter((x) => x.kind === "sessions" && (["spec", "plan"].includes(x.node_id) || String(x.hook_point).endsWith(".auto_review")))) {
        if ((await call(`/documents/${d.document_id}`)).body.content.includes("the plan names")) wrong.push(`${b.item.title}: ${d.title}`);
      }
      // The implementer's summary lists the files its plan named, not those verification's round added after it; any other
      // session counts the files its plan (or, with no plan, its spec) names, and an item with no plan names none.
      const files = b.content!.files as Row[];
      const planned = defIds(b).includes("plan");
      for (const d of (S.docs[b.item.id] ?? []).filter((x) => x.kind === "sessions" && S.docText?.[x.document_id] == null)) {
        const text = (await call(`/documents/${d.document_id}`)).body.content as string;
        const said = /\bon the (\d+) (\w+) the (plan|spec) names\./.exec(text);
        if (said) counts++;
        if ((d.hook_point === "implementation.main.implement" && files.filter(late).some(([p]) => text.includes(p)))
          || (said && JSON.stringify(said.slice(1)) !== JSON.stringify([String(files.filter((f) => !late(f)).length), "files", planned ? "plan" : "spec"]))
          || (!planned && /\bthe plan\b/.test(text))) wrong.push(`${b.item.title}: ${d.title}`);
      }
    }
    expect([counts > 0, briefs > 0]).toEqual([true, true]);
    expect(wrong).toEqual([]);
  });
  // A brief written while verification's round has added a file no repair has landed leaves that file out, and counts
  // only what it lists. No board item reaches a brief so; this one is the gated row with such a file added.
  it.runIf(sample)("a review brief written before a repair landed its round's file lists and counts only what landed", () => {
    const b = board.find((x) => x.item.bead_id === "kraft-cb59")!;
    const files = [...(b.content!.files as Row[]), ["tests/test_cache_bound.py", 9, 0, "late"]] as Row[];
    const text = docBody({ ...b, content: { ...b.content!, files: files as any } }, { kind: "review_briefs" });
    expect(briefProblem(text, "review_brief", files.filter((f) => !late(f)))).toBeNull();
    expect(text).not.toContain("tests/test_cache_bound.py");
  });
  it("a kind the item's chain neither produces nor gates is a 404; its merge request's metadata is its own", async () => {
    const one = board.find((b) => b.item.chain_template === "default" && completed(b, "describe_merge_request"))!;
    const docs = board.find((b) => b.item.chain_template === "docs_only")!;
    expect([(await call(`/work-items/${one.item.id}/artifacts/nonsense`)).status, (await call(`/work-items/${docs.item.id}/artifacts/plan`)).status, (await call(`/work-items/${docs.item.id}/artifacts/spec`)).status]).toEqual([404, 404, 200]);
    const meta = (await call(`/work-items/${one.item.id}/artifacts/mr_meta`)).body;
    expect([meta.title, meta.content.startsWith("---"), meta.content.includes(one.item.description), meta.content.includes(one.content!.files[0][0])]).toEqual([one.item.title, false, true, true]);
  });
  // `get_produced_artifact`: a kind is on disk once the node that produces it has completed (a pending gate's is too);
  // before that, with nothing indexed, it is a 404 that says the item has none.
  it("a kind its chain produces is served once the node that writes it has completed, and is a 404 before", async () => {
    const wrong: string[] = [];
    let before = 0;
    for (const b of board) {
      const nodes: any[] = JSON.parse(b.item.materialized_chain).chain.nodes;
      for (const n of nodes) for (const [, kind] of JSON.stringify(n).matchAll(/"produces":"([^"]+)"/g)) {
        const a = await call(`/work-items/${b.item.id}/artifacts/${kind}`);
        const due = completed(b, n.id) || (!!b.item.pending_gate && b.item.gate_artifact === `items/${b.item.id}/docs/${kind}.md`);
        if (!due) before++;
        if (due ? a.status !== 200 : a.status !== 404 || a.body.detail !== `this work item has no ${kind}`) wrong.push(`${b.item.title}: ${kind} ${a.status}`);
      }
    }
    expect(before).toBeGreaterThan(0);
    expect(wrong).toEqual([]);
  });
  it("search finds nothing for a query nothing holds, nor for a blank one, and only board items' documents for one many do", async () => {
    expect((await call("/search?q=zzzqx")).body.results).toEqual([]);
    expect([(await call("/search?q=")).status, (await call("/search?q=%20")).body.detail]).toEqual([422, "q is required"]);
    // `limit: int`: a limit that is no integer, an empty or a fractional one too, is refused before the route runs, a list
    // of what failed naming what was sent (FastAPI, as a `limit: int` route answers each of these).
    for (const lim of ["abc", "", "1.5", "1.", "3__0", "1.0_0"]) {
      const bad = await call(`/search?q=the&limit=${lim}`);
      const [d] = Array.isArray(bad.body.detail) ? bad.body.detail : [{}];
      expect([bad.status, d.type, d.loc, d.input], lim).toEqual([422, "int_parsing", ["query", "limit"], lim]);
    }
    const ids = new Set(board.map((b) => b.item.id));
    const hits = (await call("/search?q=session")).body.results;
    expect(hits.length).toBeGreaterThan(0);
    expect(hits.filter((r: any) => !ids.has(itemOf(r))).map((r: any) => r.title)).toEqual([]);
  });
  it("search for an item's title puts that item's documents first", async () => {
    const wrong = await Promise.all(board.filter((b) => S.docs[b.item.id]?.length).map(async (b) => {
      const hits = (await call(`/search?q=${encodeURIComponent(b.item.title)}`)).body.results;
      const first = hits.slice(0, Math.min(6, S.docs[b.item.id].length));
      return first.length && first.every((r: any) => itemOf(r) === b.item.id) ? null : b.item.title;
    }));
    expect(wrong.filter(Boolean)).toEqual([]);
  });
  // The server's `_fts` orders by `bm25(documents_fts)`, every column weighed alike, with no boost for a phrase; the demo
  // ranks by a heuristic in its place: the whole query in a title first, then how many of its words a title holds (the
  // `searchFor` rows below pin both tiers). A hit whose title holds the query comes before any whose title does not. FTS5
  // folds case, so a query's case changes nothing.
  it("search puts a hit whose title holds the query, a word or a phrase, before one whose title does not, whatever the query's case", async () => {
    const all = (q: string) => call(`/search?q=${encodeURIComponent(q)}&limit=100`).then((r) => r.body.results as any[]);
    const holds = (title: string, q: string) => ` ${title.toLowerCase().match(/[\p{L}\p{N}]+/gu)!.join(" ")} `.includes(` ${q} `);
    for (const q of ["cache", "search index"]) {
      const hits = await all(q);
      const marks = hits.map((r) => holds(r.title, q));
      expect([marks.includes(true), marks.includes(false)], q).toEqual([true, true]);
      expect(marks.lastIndexOf(true), q).toBeLessThan(marks.indexOf(false));
    }
    expect((await all("CACHE")).map((r) => r.id)).toEqual((await all("cache")).map((r) => r.id));
  });
  // `snippet(..., 64)`: the window opens a little before the first match, at most eight tokens, and marks it; one near
  // the text's end opens earlier, to hold its last 64 tokens.
  it("a search snippet marks the first match in its document and opens at most eight tokens before it", async () => {
    const hits = (await call("/search?q=cache&limit=100")).body.results as any[];
    expect(hits.length).toBeGreaterThan(0);
    const wrong: string[] = [];
    for (const r of hits) {
      const text = (await call(`/documents/${encodeURIComponent(r.id)}`)).body.content as string;
      const first = text.match(/[\p{L}\p{N}]+/gu)!.find((w) => w.toLowerCase() === "cache");
      const at = r.snippet.indexOf("[");
      const before = r.snippet.slice(0, at).match(/[\p{L}\p{N}]+/gu) ?? [];
      if (at < 0 || !r.snippet.startsWith(`[${first}]`, at) || (before.length > 8 && r.snippet.endsWith("…"))) wrong.push(`${r.title}: ${r.snippet.slice(0, 60)}`);
    }
    expect(wrong).toEqual([]);
  });
  it("search ranks a document before a session's summary when they match alike", async () => {
    for (const q of ["cache", "brief"]) expect((await call(`/search?q=${q}`)).body.results[0]?.source_kind, q).toBe("artifact");
  });
  it("search matches every word of the query, whole, in a title or text, not a path, and marks them in the snippet", async () => {
    const all = (q: string) => call(`/search?q=${encodeURIComponent(q)}&limit=100`).then((r) => r.body.results);
    const both = await all("cache hash");
    const either = [...(await all("cache")), ...(await all("hash"))];
    expect(both.length).toBeGreaterThan(0);
    expect(both.length).toBeLessThan(new Set(either.map((r: any) => r.id)).size);
    expect(both.filter((r: any) => !/\[cache\]/i.test(r.snippet) && !/\bcache\b/i.test(r.title)).map((r: any) => r.title)).toEqual([]);
    expect(both.filter((r: any) => !/\[(cache|hash)\]/i.test(r.snippet)).map((r: any) => r.title)).toEqual([]);
    // A word inside another is no match; nor is the item's id, which only its documents' paths hold.
    expect([(await all("cach")).length, (await all(board.find((b) => S.docs[b.item.id]?.length)!.item.id)).length]).toEqual([0, 0]);
  });
  it("search narrows by source kind, kind, repo and limit", async () => {
    const q = "/search?q=the&limit=100";
    const every = (await call(q)).body.results;
    const repo = board.find((b) => S.docs[b.item.id]?.length)!.item.repo;
    const by = async (f: string) => (await call(`${q}&${f}`)).body.results;
    const [summaries, specs, inRepo, three] = await Promise.all([by("source_kind=session_summary"), by("kind=specs"), by(`repo=${encodeURIComponent(repo)}`), call("/search?q=the&limit=3").then((r) => r.body.results)]);
    const keeps = [(r: any) => r.source_kind === "session_summary", (r: any) => r.kind === "specs", (r: any) => r.repo === repo];
    // Each filter keeps some of what the query finds, and only those.
    expect(keeps.map((k, n) => every.some(k) && !every.every(k) && [summaries, specs, inRepo][n].length > 0 && [summaries, specs, inRepo][n].every(k))).toEqual([true, true, true]);
    expect(three.length).toBe(3);
    expect((await call("/search?q=the")).body.results.length).toBe(Math.min(20, every.length));
    // `search.py` clamps the limit it is asked for to 1..100.
    expect(await Promise.all(["0", "-5", "500"].map(async (n) => (await call(`/search?q=the&limit=${n}`)).body.results.length))).toEqual([1, 1, Math.min(100, every.length)]);
    // Pydantic's lax int, as FastAPI parses `limit: int`: underscores between digits, and a fraction of zeros.
    expect(await Promise.all(["1.0", "3_0", " 2.00 ", "1_0_0", "+3"].map(async (n) => (await call(`/search?q=the&limit=${encodeURIComponent(n)}`)).body.results.length))).toEqual([1, 30, 2, 100, 3]);
  });
  it("it has run from its first node to the clock, or to its last event once it ended, and was last touched no earlier than its last event", () => {
    const wrong = board.filter((b) => b.events.some((e) => e.type === "node_started")).flatMap((b) => {
      const it = b.item;
      const first = b.events.find((e) => e.type === "node_started")!.created_at;
      const ended = ENDED_STATUSES.includes(it.status);
      return (it.started_at ?? it.created_at) !== first || (ended && (it.ended_at ?? it.updated_at) !== b.events.at(-1)!.created_at) || it.updated_at < b.events.at(-1)!.created_at ? [it.title] : [];
    });
    expect(wrong).toEqual([]);
  });
  // The sample's histories end when each item was last touched, before the clock: none ends at it by accident. The walk's
  // clock moves with it, so an item it touched last ends at its moment.
  it.runIf(sample)("no history in the sample ends at the clock", () => {
    expect(board.filter((b) => b.events.length && b.events.at(-1)!.created_at >= NG_NOW).map((b) => b.item.title)).toEqual([]);
  });
  // Every started item shows a running time: an ended one its span, a live one the clock less its start, so no two live
  // ones may have started at the same moment. A never-started item (f5d3) shows none and is exempt.
  it("no two started board items show the same running time", () => {
    const ended = board.filter((b) => ENDED_STATUSES.includes(b.item.status) && b.item.started_at).map((b) => Date.parse(b.item.ended_at ?? b.item.updated_at) - Date.parse(b.item.started_at));
    const live = board.filter((b) => !ENDED_STATUSES.includes(b.item.status) && b.item.started_at).map((b) => b.item.started_at);
    expect([ended.length > 1, live.length > 1]).toEqual([true, true]);
    expect([ended.filter((x, k) => ended.indexOf(x) !== k), live.filter((x, k) => live.indexOf(x) !== k)]).toEqual([[], []]);
  });
});

/** The items a walk files when nothing else is on the board, `n` of them: each completed by a door the tick it is filed. */
const filings = new Map<number, ItemBundle[]>();
const longWalk = (n: number): ItemBundle[] => {
  if (filings.has(n)) return filings.get(n)!;
  vi.useFakeTimers();
  try {
    const S = buildScenario("default");
    S.ngBoard = [];
    const T = transitions(S, { now: () => NG_NOW });
    const m = motion(S, T, { random: () => 0 });
    for (let k = 0; k < n * 3 && S.ngBoard.length < n; k++) {
      vi.advanceTimersByTime(8_000);
      for (const id of S.ngBoard) if (S.bundles[id].item.status === "active") T.markCompleted(id);
    }
    m.stop();
    filings.set(n, S.ngBoard.map((id) => S.bundles[id]));
    return filings.get(n)!;
  } finally {
    vi.useRealTimers();
  }
};

// The indexer refiles what the item's nodes and sessions wrote, and leaves what came another way: a summary with a body
// of its own (an escalation reply), and a document `itemDocs` does not make (one filed at intake, `_attachment_docs`).
describe("mergeDocs", () => {
  const doc = (document_id: string, path: string, title = path) => ({ document_id, path, title });
  it.each<[string, any[], any[], Record<string, string>, string[]]>([
    ["a refiled path takes the fresh copy", [doc("a", "p", "old")], [doc("a", "p", "new")], {}, ["a:new"]],
    ["a path filed with a body of its own keeps it", [doc("r", "p", "reply")], [doc("x", "p", "summary")], { r: "# reply" }, ["r:reply"]],
    ["a document filed another way stays", [doc("s", "docs/spec.md", "attached")], [doc("b", "p")], {}, ["s:attached", "b:p"]],
    ["a new path is added", [], [doc("b", "p")], {}, ["b:p"]],
  ])("%s", (_name, had, fresh, text, want) => {
    expect(mergeDocs(had, fresh, text).map((d) => `${d.document_id}:${d.title}`)).toEqual(want);
  });
});

describe("the content table, the entries the walk never reaches included", () => {
  const B = buildScenario("default");
  const rows = [
    ...[...B.ngBoard, ...B.ngArchived].map((id) => B.bundles[id].item).map((i) => ({ name: i.title, repo: i.repo.split("/").pop()!, docs: i.repo.endsWith("/kraft-docs") || i.chain_template === "docs_only", c: CONTENT[i.bead_id] })),
    ...FRESH_TITLES.map((title) => ({ name: title, repo: FRESH_CONTENT[title].repo!, docs: FRESH_CONTENT[title].repo === "kraft-docs", c: FRESH_CONTENT[title] })),
  ];
  // What a long walk files: each story again and again, one filing a tick, each completed by a door at once so the next
  // tick files the next (motion files only when nothing is active). Fifteen filings: every story three times.
  it("a long walk files each story in its own repo every time, and no two of its items or the board's in one repo share a merge request", () => {
    const filed = longWalk(15);
    expect(filed.length).toBe(15);
    expect(filed.filter((b) => b.item.repo !== `/Users/dev/code/${FRESH_CONTENT[b.item.title].repo}` || b.content!.files !== FRESH_CONTENT[b.item.title].files).map((b) => b.item.title)).toEqual([]);
    const keys = [...rows.slice(0, -FRESH_TITLES.length).map((r) => `${r.repo}#${r.c.mr}`), ...filed.map((b) => `${b.item.repo.split("/").pop()}#${b.content!.mr}`)];
    expect(keys.filter((x, k) => keys.indexOf(x) !== k)).toEqual([]);
    expect(filed.filter((b) => b.content!.mr >= 150 && b.content!.mr <= 159).map((b) => b.item.title)).toEqual([]);
  });
  it("has an entry for every board row and every title the walk refiles", () => {
    expect(rows.filter((r) => !r.c).map((r) => r.name)).toEqual([]);
  });
  // A late file is what the walk's red first round reports (`redOf`), and only its first filing goes red.
  it("only the walk's first story carries a late file", () => {
    expect(FRESH_TITLES.filter((title) => FRESH_CONTENT[title].files.some(isLate))).toEqual([FRESH_TITLES[0]]);
  });
  it("every entry has its own brief", () => {
    const briefs = rows.map((r) => r.c.description);
    expect(briefs.filter((x, k) => briefs.indexOf(x) !== k)).toEqual([]);
  });
  it("no two entries in one repo share a merge request number, and none is in the walk's own range", () => {
    const keys = rows.map((r) => `${r.repo}#${r.c.mr}`);
    expect(keys.filter((x, k) => keys.indexOf(x) !== k)).toEqual([]);
    expect(rows.filter((r) => r.c.mr >= 150 && r.c.mr <= 159).map((r) => r.name)).toEqual([]);
  });
  // The one definition the rules and the mock read, held to a table of its own: a page anywhere, a site file under
  // `docs/`, and not a program there or a file outside it.
  it.each<[string, boolean]>([["docs/_nav.py", false], ["src/a.ts", false], ["plugins/search/cache.py", false], ["src/docs/x.css", false], ["docs/x.css", true], ["README.md", true], ["docs/_nav.yml", true], ["docs/_nav.yaml", true], ["docs/a.js", true], ["docs/a.png", true]])("a docs path: %s is %s", (path, docs) => {
    expect(isDocsPath(path)).toBe(docs);
  });
  // A docs item changes docs only, and an item that changes only docs is one; a code item changes a test; an item that
  // removes or drops something takes out more than it adds.
  it("its files fit its kind", () => {
    const files = (r: (typeof rows)[number]) => r.c.files as Row[];
    expect(rows.filter((r) => r.docs !== docsOnly(files(r))).map((r) => r.name)).toEqual([]);
    expect(rows.filter((r) => !r.docs && !files(r).some(([p]) => isTestPath(p))).map((r) => r.name)).toEqual([]);
    expect(rows.filter((r) => /^(Remove|Drop)\b/.test(r.name) && files(r).reduce((a, [, i, d]) => a + i - d, 0) >= 0).map((r) => r.name)).toEqual([]);
  });
  // A new file removes nothing and a deleted one adds nothing; a hunk written out shows no more rows of a side than its
  // file's stat counts, and its file is one the item changes.
  it("each file's counts fit how it changes, and a written-out hunk its file's counts", () => {
    expect(rows.flatMap((r) => (r.c.files as Row[]).filter(([, i, d, ...f]) => (f.includes("new") && d !== 0) || (f.includes("deleted") && i !== 0)).map(([p]) => `${r.name}: ${p}`))).toEqual([]);
    const hunks = rows.flatMap((r) => Object.entries(r.c.hunks ?? {}).map(([p, h]) => ({ r, h, f: (r.c.files as Row[]).find(([x]) => x === p) })));
    expect(hunks.length).toBeGreaterThan(0);
    const marks = (h: { rows: string[] }, m: string) => h.rows.filter((x) => x[0] === m).length;
    expect(hunks.filter(({ h, f }) => !f || marks(h, "+") > f[1] || marks(h, "-") > f[2]).map(({ r }) => r.name)).toEqual([]);
  });
});

/** A finished item's event types node by node: each node's from its `node_started` until the walk starts the next. A forge
 *  wait reads as one pending poll then the settled one, however many polls it took: a poller's re-entering
 *  `node_started` is dropped, and a pending poll the same as the one before it. */
const byNode = (b: ItemBundle) => {
  const out = new Map<string, string[]>();
  let at: string | null = null;
  for (const e of b.events) {
    if (e.type === "work_item_completed") break;
    if (e.type === "node_started") at = String(e.payload.node_id);
    if (at) out.set(at, [...(out.get(at) ?? []), e.type]);
  }
  const POLL = "external_wait_observed,worker_session_exited,work_item_waiting";
  for (const [node, types] of out) {
    const kept: string[] = [];
    types.forEach((type, k) => {
      if (type === "node_started" && types[k + 1] === "external_wait_observed") return;
      kept.push(type);
      const n = kept.length;
      if (n >= 6 && kept.slice(n - 3).join() === POLL && kept.slice(n - 6, n - 3).join() === POLL) kept.splice(n - 3, 3);
    });
    out.set(node, kept);
  }
  return out;
};

/** The forge tasks' log lines on the board that name a merge request other than their own item's. */
const strayMrs = (S: Scenario) => [...S.ngBoard, ...S.ngArchived].map((id) => S.bundles[id]).filter((b) => b.item.mr_ref).flatMap((b) => b.sessions.filter((s) => !agentic(b, s)).flatMap((s) =>
  logOf(b, s).filter((l) => (l.text.match(MR_URL) ?? []).some((u: string) => u !== b.item.mr_ref.url)).map((l) => `${b.item.title}: ${l.text}`)));

/** A walk read after every tick, until it has filed an item of its own and walked it through CI (the first it files,
 *  whose first round of tests fails and is repaired): what each rule found wrong at any tick, and how many ticks met the
 *  state each rule is about. Built once. */
type Ticked = { wrong: Record<string, Set<string>>; met: Record<string, number>; ciDone: boolean };
let ticked: Promise<Ticked> | null = null;
const tickWalk = (): Promise<Ticked> => (ticked ??= (async () => {
  vi.useFakeTimers();
  try {
    const S = buildScenario("default");
    let at = Date.parse(NG_NOW);
    const T = transitions(S, { now: () => new Date(at).toISOString() });
    const serve = mockServer(S, demoOptions);
    const get = async (path: string) => JSON.parse(((await serve({ method: "GET", url: `http://demo/api${path}`, json: () => null })) as Answer).body!);
    for (const id of S.ngBoard) if (S.bundles[id].item.pending_gate) T.approve(id, S.bundles[id].item.pending_gate);
    // The sample rows a repair had already landed before the walk began: a late file they show is not the walk's.
    const landedBefore = new Set(S.ngBoard.filter((id) => repaired(S.bundles[id])));
    const m = motion(S, T, { random: () => 0 });
    const wrong: Ticked["wrong"] = { mrs: new Set(), late: new Set(), progress: new Set() };
    const met: Ticked["met"] = { waited: 0, repairing: 0, landed: 0, reported: 0 };
    const ciDone = () => S.ngBoard.some((id) => FRESH_TITLES.includes(S.bundles[id].item.title) && completed(S.bundles[id], "merge_request_feedback"));
    for (let k = 0; k < 400 && !ciDone(); k++) {
      at += 8_000;
      vi.advanceTimersByTime(8_000);
      for (const id of S.ngBoard) {
        const b = S.bundles[id];
        if (b.item.pending_gate && b.events.at(-1)?.type === "gate_auto_review_skipped") T.approve(id, b.item.pending_gate);
        if (b.item.status === "waiting" && b.sessions.some((s) => s.status === "waiting" && logOf(b, s).some((l) => l.text.match(MR_URL)))) met.waited++;
      }
      for (const x of strayMrs(S)) wrong.mrs.add(x);
      // Verification's round lands its files and its fix commit with a repair's end, not before: none while its repair
      // runs, and none on an item that has had no repair at all.
      for (const id of [...S.ngBoard, ...S.ngArchived]) {
        const b = S.bundles[id];
        if (!b.content || b.item.worktree_exists === false) continue;
        const d = (await get(`/work-items/${id}/diff`)).landed;
        const lateNames = (b.content.files as Row[]).filter(late).map(([p]) => p);
        const shown = d.files.some((f: any) => lateNames.includes(f.path)) || d.commits.some((c: string) => c.endsWith(" Fix what verification found"));
        if (b.sessions.some((s) => s.hook_point === "verification.fix_loop.main.repair" && s.status !== "done")) met.repairing++;
        if (shown && repaired(b) && !landedBefore.has(id)) met.landed++;
        if (shown && !repaired(b)) wrong.late.add(`${b.item.title} at tick ${k}`);
      }
      // `progress.for_item`: while an item with a plan is active on its implementation node, its board row and its detail
      // say the task its implementer last reported starting this run (the first before it reports one); otherwise none.
      const rows = (await get("/work-items")).items as any[];
      for (const id of S.ngBoard) {
        const b = S.bundles[id];
        const runs = b.item.status === "active" && b.item.current_node_id === "implementation" && defIds(b).includes("plan");
        const from = b.events.map((e) => e.type === "node_started" && e.payload.node_id === "implementation").lastIndexOf(true);
        const told = b.events.slice(from + 1).filter((e) => e.type === "plan_progress").at(-1)?.payload;
        if (runs && told) met.reported++;
        const at = (p: any) => (p == null ? null : runs && !told ? p.current : [p.current, p.total, p.title]);
        const want = !runs ? null : told ? [told.task, told.total, told.title] : 1;
        const row = rows.find((r) => r.id === id);
        if ([row?.progress, b.item.progress].some((p) => JSON.stringify(at(p)) !== JSON.stringify(want))) wrong.progress.add(`${b.item.title} at tick ${k}: ${JSON.stringify(row?.progress)}`);
        // The row's progress is position and title only (`board._board_progress`); the detail's lists every task of the
        // plan, one per file it changes then a check, each done below the current one and pending above it.
        if (row?.progress && JSON.stringify(Object.keys(row.progress).sort()) !== '["current","title","total"]') wrong.progress.add(`${b.item.title} at tick ${k}: the row lists ${Object.keys(row.progress)}`);
        const p = b.item.progress;
        const plan = [...(b.content!.files as Row[]).filter((f) => !late(f)).map(([x]) => `Change ${x}`), "Check it"];
        if (p && JSON.stringify(p.tasks.map((x: any) => [x.n, x.title, x.state])) !== JSON.stringify(plan.map((x, n) => [n + 1, x, n + 1 < p.current ? "done" : n + 1 === p.current ? "current" : "pending"]))) wrong.progress.add(`${b.item.title} at tick ${k}: tasks ${JSON.stringify(p.tasks)}`);
      }
    }
    m.stop();
    return { wrong, met, ciDone: ciDone() };
  } finally {
    vi.useRealTimers();
  }
})());

// A log is rewritten as its session goes on (made, waiting, ended), so the end state alone misses one that named another
// item's merge request on the way: the board is read after every tick of a walk.
describe("at every tick of the walk", () => {
  it("a forge task's log names its own item's merge request, until the walk has filed and walked an item of its own through CI", async () => {
    const { wrong, met, ciDone } = await tickWalk();
    expect([ciDone, met.waited > 0]).toEqual([true, true]);
    expect([...wrong.mrs]).toEqual([]);
  });
  it("an item's diff shows verification's late files and fix commit only once a repair of its has finished", async () => {
    const { wrong, met } = await tickWalk();
    expect([met.repairing > 0, met.landed > 0]).toEqual([true, true]);
    expect([...wrong.late]).toEqual([]);
  });
  it("a board row and its detail show plan progress only while the item runs its implementation, at the task last reported", async () => {
    const { wrong, met } = await tickWalk();
    expect(met.reported).toBeGreaterThan(0);
    expect([...wrong.progress]).toEqual([]);
  });
  // `combine`: a retried run has reported nothing yet, so the task after the last its commits name is current; a pause
  // gives up the item's slot, and with it its progress (`active_implementation_node`).
  it("an item retried on its implementation shows the task after its last commit, and none once it is paused", () => {
    const S = buildScenario("default");
    const T = transitions(S);
    const b = S.bundles[byBead(S, "kraft-7d21")];
    expect(T.retry(b.item.id).status).toBe(200);
    // The walk enters the node again, a fresh run with no report yet.
    T.enterNode(b.item.id, "implementation");
    const shown = () => b.item.progress && [b.item.progress.current, b.item.progress.total, b.item.progress.title];
    expect([b.item.status, shown()]).toEqual(["active", [3, 4, "Change tests/workers/test_pool.py"]]);
    expect(b.item.progress.tasks.map((x: any) => [x.n, x.title, x.state])).toEqual([
      [1, "Change kraft/workers/pool.py", "done"], [2, "Change kraft/workers/events.py", "done"], [3, "Change tests/workers/test_pool.py", "current"], [4, "Check it", "pending"],
    ]);
    expect(T.pause(b.item.id).status).toBe(200);
    expect([b.item.status, shown()]).toEqual(["paused", null]);
  });
  // `is_rework`: a run a gate's rejection sent back to the implementation follows the note, not the plan.
  it("an item a rejection sent back to its implementation shows no plan progress", () => {
    const S = buildScenario("default");
    const T = transitions(S);
    const b = S.bundles[byBead(S, "kraft-cb59")];
    expect(T.reject(b.item.id, "final_review", "again", "implementation").status).toBe(200);
    T.enterNode(b.item.id, "implementation");
    expect([b.item.status, b.item.current_node_id, b.item.progress]).toEqual(["active", "implementation", null]);
  });
});

describe("the live walk runs each node as the sample's finished item ran it", () => {
  const S = walked();
  const fresh = (n: number) => S.bundles[S.ngBoard.find((id) => S.bundles[id].item.title === FRESH_TITLES[n])!];
  const sample = byNode(S.bundles[S.ng.done]);
  it("walks both items it filed to the end of the default chain", () => {
    expect([0, 1].map((n) => [fresh(n).item.status, fresh(n).item.chain_template, [...byNode(fresh(n)).keys()]])).toEqual([0, 1].map(() => ["completed", "default", CHAIN_NODES.default]));
  });
  // The seeds and the walk read one recipe (nodeRecipe.ts); this holds what each user does with it to the same events.
  // Each item reports its own plan's tasks, so how many plan_progress events it has is its own (the literal rows hold that).
  const unplanned = (types?: string[]) => types?.filter((x) => x !== "plan_progress");
  it.each(CHAIN_NODES.default)("%s", (node) => {
    expect(sample.get(node)).toBeDefined();
    expect(unplanned(byNode(fresh(1)).get(node))).toEqual(unplanned(sample.get(node)));
    if (node !== "verification") expect(unplanned(byNode(fresh(0)).get(node))).toEqual(unplanned(sample.get(node)));
  });
  it("verification whose first round of tests failed: the running sample's fix cycle and second round, then that round's end", () => {
    const failed = byNode(fresh(0)).get("verification")!;
    const running = byNode(S.bundles[S.ng.running]).get("verification")!;
    expect(running).toContain("fix_cycle_finished");
    expect(failed.slice(0, running.length)).toEqual(running);
    expect(failed.slice(running.length)).toEqual(["worker_session_exited", "findings_measured", "node_completed"]);
  });
  it("every session it made has its task's kind, an agent's reports what its task's recipe says, and a command's ran its own", () => {
    /** What a command's session ran: a changed-test-scope pass's sessions each the scope command its round picked, in
     *  turn (`test_scopes_selected`); any other task's its own run's. */
    const commandOf = (b: ItemBundle, s: any) => {
      if (taskRun(b.item.materialized_chain, s.hook_point) !== "kraft.verify_changed_test_scopes") return RUNS[taskRun(b.item.materialized_chain, s.hook_point) ?? ""]?.command ?? null;
      const picked = b.events.find((e) => e.type === "test_scopes_selected" && e.payload.round === s.round)?.payload.commands as string[] | undefined;
      return picked?.[b.sessions.filter((x) => x.hook_point === s.hook_point && x.round === s.round).indexOf(s)];
    };
    const wrong = [0, 1].map(fresh).flatMap((b) => b.sessions.filter((s) => {
      const agent = agentic(b, s);
      if ((s.harness === "claude") !== agent) return true;
      if (!agent || noAgentTriage(b, s)) return s.model != null || s.cost_usd != null || s.command !== commandOf(b, s);
      const u = agentUsage(s.hook_point, b.sessions.indexOf(s));
      return s.model !== u.model || s.tokens_in !== u.tokens_in || s.tokens_out !== u.tokens_out || s.cost_usd !== u.cost_usd;
    }).map((s) => `${b.item.title}: ${s.hook_point}`));
    expect(wrong).toEqual([]);
  });
});

/** An item's events node by node, from a node's `node_started` until the walk starts another: each its type, and
 *  `@` its `node_id` column where that is not the node's own. */
const segments = (b: ItemBundle) => {
  const out = new Map<string, string[]>();
  let at: string | null = null;
  for (const e of b.events) {
    if (e.type === "work_item_completed") break;
    if (e.type === "node_started") at = String(e.payload.node_id);
    if (at) out.set(at, [...(out.get(at) ?? []), e.node_id === at ? e.type : `${e.type}@${e.node_id}`]);
  }
  return out;
};
// What the server writes for each node of the default chain, in its order: written out, not read off the recipe, so a
// beat the recipe drops or moves fails here though the seeds and the walk, which share it, still agree.
const AGENT = ["worker_session_created", "worker_session_started", "worker_session_exited@null"];
const COMMAND = ["worker_session_created", "worker_session_exited@null"];
/** A gate asked, its reviewer run and ready, then approved by a person (the harness) and completed. */
const GATE = ["node_started", "gate_requested", "worker_session_created", "gate_auto_review_started", "worker_session_started", "worker_session_exited@null", "gate_auto_review_skipped", "gate_approved", "node_completed"];
/** A forge wait from its session's birth: its start, then `polls` looks, each pending one's waiting exit and its
 *  poller's re-entry (`node_started`, no event of its own), the last settling it and ending its session. */
const WAIT = (polls: number) => ["external_wait_started", ...Array.from({ length: polls - 1 }, () => ["external_wait_observed", "worker_session_exited@null", "work_item_waiting", "node_started"]).flat(), "external_wait_observed", "external_wait_ended", "worker_session_exited@null"];
/** A changed-test-scope pass: what it picked, then a session per scope command (the repo's tests, the docs check). */
const SCOPES = ["test_scopes_selected", ...COMMAND, ...COMMAND];
/** `ci` and `post` are how many looks the CI and post-merge CI waits take; `plan`, how many tasks the implementer reports. */
const NODE_EVENTS: Record<string, (ci: number, post: number, plan: number) => string[]> = {
  spec: () => ["node_started", ...AGENT, "node_completed"],
  spec_approval: () => GATE,
  plan: () => ["node_started", ...AGENT, "node_completed"],
  plan_approval: () => GATE,
  chain_revision: () => ["node_started", ...AGENT, "node_completed"],
  // It changed nothing, so its gate passes itself (`chain_revision_unchanged`), with no request.
  chain_revision_approval: () => ["node_started", "chain_revision_unchanged", "gate_approved", "node_completed"],
  implementation: (_ci, _post, plan) => ["node_started", "worker_session_created", "worker_session_started", ...Array(plan).fill("plan_progress"), "worker_session_exited@null", "node_completed"],
  // Both scopes pass, then the code review, then the round's measurement.
  verification: () => ["node_started", ...SCOPES, ...AGENT, "findings_measured", "node_completed"],
  work_brief: () => ["node_started", ...AGENT, "node_completed"],
  local_review: () => GATE,
  describe_merge_request: () => ["node_started", ...AGENT, "node_completed"],
  // The rebase, then the open task, which pushes the branch and opens the merge request in its own session.
  draft_merge_request: () => ["node_started", ...COMMAND, "worker_session_created", "branch_pushed", "mr_opened", "worker_session_exited@null", "node_completed"],
  // CI, then the review wait, which finds no reviewer and settles at its first look, then triage with nothing to read.
  merge_request_feedback: (ci) => ["node_started", "worker_session_created", ...WAIT(ci), "worker_session_created", "automated_review_not_configured", ...WAIT(1), ...COMMAND, "node_completed"],
  work_item_summary: () => ["node_started", ...AGENT, "node_completed"],
  final_review: () => GATE,
  mark_ready: () => ["node_started", ...COMMAND, "node_completed"],
  external_approval: () => ["node_started", "worker_session_created", ...WAIT(1), "node_completed"],
  merge: () => ["node_started", "worker_session_created", ...WAIT(1), "node_completed"],
  post_merge_ci: (_, post) => ["node_started", "worker_session_created", ...WAIT(post), "node_completed"],
};
/** Verification whose first round of tests failed: the failure measured, a fix cycle's repair, then a passing round. */
const RED_VERIFICATION = ["node_started", ...SCOPES, "findings_measured", "fix_cycle_started", ...AGENT, "fix_cycle_finished", ...SCOPES, ...AGENT, "findings_measured", "node_completed"];

describe("each node of the default chain writes what the server writes, in its order", () => {
  const S = walked();
  const fresh = (n: number) => S.bundles[S.ngBoard.find((id) => S.bundles[id].item.title === FRESH_TITLES[n])!];
  // The seeds' CI waits look three times and their post-merge CI four; the walk's every wait twice (motion.ts `POLLS`),
  // written here as numbers so a change to either fails. Each implementer reports its own plan's tasks: the sample's six
  // (it has no content), the first walked item's four (its three files, then `Check it`; its fourth is verification's
  // round's), the second's five (its four files, then `Check it`).
  it.each(CHAIN_NODES.default)("%s", (node) => {
    expect(segments(S.bundles[S.ng.done]).get(node)).toEqual(NODE_EVENTS[node](3, 4, 6));
    expect(segments(fresh(1)).get(node)).toEqual(NODE_EVENTS[node](2, 2, 5));
    expect(segments(fresh(0)).get(node)).toEqual(node === "verification" ? RED_VERIFICATION : NODE_EVENTS[node](2, 2, 4));
  });
  // `WaitBounds.interval_after`: 30 s after the first pending look, doubling; `store.session_exited` stamps each exit.
  it("a seed's pending looks come due on the wait's backoff, and its waiting session's exit is stamped", () => {
    const b = S.bundles[S.ng.done];
    const gaps = (task: string) => b.events.filter((e) => e.type === "external_wait_observed" && e.payload.task === task && e.payload.state === "pending")
      .map((e) => (Date.parse(String(e.payload.next_observation_at)) - Date.parse(e.created_at)) / 1000);
    expect([gaps("merge_request_feedback.ci.await_ci"), gaps("post_merge_ci.main.await")]).toEqual([[30, 60], [30, 60, 120]]);
    const w = S.bundles[S.ng["waiting-ci"]];
    const s = w.sessions.find((x) => x.status === "waiting")!;
    expect(s.exited_at).toBe(w.events.filter((e) => e.type === "worker_session_exited" && e.payload.session_id === s.id).at(-1)!.created_at);
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

/** `ForgeTask.wait_bounds` (`config/library.yaml`): a wait's timeout, first interval and longest interval, in seconds, by what it watches. */
const BOUNDS = [
  ["mr.ci", 5_400, 30, 300], ["mr.post_merge_ci", 5_400, 30, 300], ["mr.merge", 5_400, 30, 300],
  ["mr.automated_review", 1_800, 30, 300], ["mr.external_approval", 604_800, 300, 3_600],
] as const;
describe("a forge wait starts with its task's bounds", () => {
  const W = walked();
  const bundles = [W.bundles[W.ng.done], ...W.ngBoard.map((id) => W.bundles[id]).filter((b) => FRESH_TITLES.includes(b.item.title))];
  it.each(BOUNDS)("%s", (kind, timeout, initial, max) => {
    const starts = bundles.flatMap((b) => b.events.filter((e) => e.type === "external_wait_started" && e.payload.kind === kind));
    expect(starts.length).toBeGreaterThanOrEqual(2);
    for (const e of starts) {
      expect(e.payload).toMatchObject({ timeout_s: timeout, initial_interval_s: initial, max_interval_s: max });
      expect(Date.parse(String(e.payload.deadline)) - Date.parse(String(e.payload.started_at))).toBe(timeout * 1000);
    }
  });
});

describe("a wait is open from its start until its end or a restart of the run", () => {
  const ev = (type: string) => ({ type, payload: { task: "t" } });
  it.each<[string, string[], number | null]>([
    ["nothing", [], null],
    ["its start", ["external_wait_started"], 0],
    ["a look", ["external_wait_started", "external_wait_observed"], 1],
    ["two looks", ["external_wait_started", "external_wait_observed", "external_wait_observed"], 2],
    ["its end", ["external_wait_started", "external_wait_observed", "external_wait_ended"], null],
    ["a retry", ["external_wait_started", "external_wait_observed", "work_item_retried"], null],
    ["a fork", ["external_wait_started", "external_wait_observed", "run_forked"], null],
    ["a base change's restart", ["external_wait_started", "external_wait_observed", "base_change_restart"], null],
    ["a merge rejection's restart", ["external_wait_started", "external_wait_observed", "merge_rejection_restart"], null],
    ["a wait begun after a restart", ["external_wait_started", "run_forked", "external_wait_started", "external_wait_observed"], 1],
  ])("after %s", (_what, types, open) => {
    expect(openWait({ events: types.map(ev) } as any, "t")).toBe(open);
  });
});

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

  it("a forge wait's session a door makes prints its own item's merge request while it runs", () => {
    const { S, T } = live();
    // A finished row with a merge request of its own, back on its CI wait as a retry would put it.
    const b = S.bundles[byBead(S, "kraft-1b6e")];
    Object.assign(b.item, { status: "active", current_node_id: "merge_request_feedback" });
    const sid = T.createSession(b.item.id, "merge_request_feedback", "merge_request_feedback.ci.await_ci").body.session_id;
    const urls = logOf(b, b.sessions.find((s) => s.id === sid)).flatMap((l) => l.text.match(MR_URL) ?? []);
    expect([b.item.mr_ref.url !== forgeUrl("/Users/dev/code/kraft-plugins", 142), urls.length > 0, urls.every((u: string) => u === b.item.mr_ref.url)]).toEqual([true, true, true]);
  });

  // An item a visitor files has no content of its own: its draft opens the walk's next number, from 150, on its repo's forge.
  it("an item the composer filed opens merge request 150 on its own repo's forge, and the next one 151", () => {
    const { S, T } = live();
    const opened = ["kraft-api", "kraft-core"].map((name) => {
      const id = T.create({ title: `Filed in ${name}`, repo: `/Users/dev/code/${name}`, chain_template: "default", autostart: true }).body.id;
      expect(S.bundles[id].content).toBeUndefined();
      T.mrOpened(id, "draft_merge_request");
      return [S.bundles[id].item.mr_ref, S.bundles[id].events.at(-1)!.payload];
    });
    const want = [{ number: 150, url: forgeUrl("/Users/dev/code/kraft-api", 150) }, { number: 151, url: forgeUrl("/Users/dev/code/kraft-core", 151) }];
    expect(opened).toEqual(want.map((mr) => [mr, mr]));
    expect(want[0].url).toContain("/-/merge_requests/150");
  });

  // The board has no row left waiting on CI, so a seed built as one stands in for what the recipe's wait writes.
  it("a seed left waiting on CI prints its own merge request in its waiting log", () => {
    const mr = mrOf("/Users/dev/code/kraft-core", CONTENT["kraft-1b6e"].mr);
    const b = buildNgItem("waiting-ci", 1999, "default", "default", mr, CONTENT["kraft-1b6e"]);
    const s = b.sessions.find((x) => x.status === "waiting")!;
    const urls = logOf(b, s).flatMap((l) => l.text.match(MR_URL) ?? []);
    expect([urls.length > 0, urls.every((u: string) => u === mr.url), mr.url === forgeUrl("/Users/dev/code/kraft-core", 104)]).toEqual([true, true, true]);
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
  // `gh.py` opens a pull at `repos/{owner}/{repo}/pulls`, `glab.py` a merge request at `projects/:id/merge_requests`.
  it.each([
    ["GitHub", "https://github.com/acme/kraft-core/pull/1", "POST repos/{owner}/{repo}/pulls: Resource not accessible by personal access token (HTTP 403)"],
    ["GitLab", "https://code.corp.example/gitlab/acme/kraft-api/-/merge_requests/1", "POST projects/:id/merge_requests: 403 Forbidden (HTTP 403)"],
  ])("a forge call's failure on %s names its route as the transport does, with the placeholders as written", (_forge, mr, said) => {
    expect(runOutput("mr.open_draft", "failed", mr)).toEqual([`draft_merge_request.open.open failed: ${said}`]);
  });

  it.each(Object.keys(RUNS))("%s: a finished run prints something", (task) => {
    expect(runOutput(task, "done", MR).length).toBeGreaterThan(0);
  });
});

// `searchFor` on documents written for each rule, so a tier or a window the board's own data happens to satisfy is still
// pinned. A two-word query whose phrase one title holds: the phrase first, then the titles holding both words, then one,
// then none; of two that match alike, a document before a session's summary, whichever was listed first.
describe("searchFor, on documents written for it", () => {
  const doc = (id: string, title: string, source_kind = "artifact") => ({ document_id: id, title, source_kind, kind: "specs", repo: "r", path: `${id}.md`, work_item_id: id });
  it("ranks the phrase in a title, then how many of the query's words a title holds, then a document before a summary", () => {
    const docs = [doc("one", "Search notes"), doc("none", "Other notes"), doc("both", "The search for a cache", "session_summary"), doc("phrase", "Cache search, the guide", "session_summary"), doc("tie-summary", "Search log", "session_summary"), doc("tie-doc", "Search guide")];
    expect(searchFor("cache search", docs, "default", () => "a cache to search").results.map((r: any) => r.id)).toEqual(["phrase", "both", "one", "tie-doc", "tie-summary", "none"]);
  });
  // `snippet(..., 64)`: the window opens eight tokens before the first match, or at the text's start; near the end it
  // opens earlier, at its last 64 tokens.
  it.each([
    ["a match near the start opens the text", 3, 3, false, true],
    ["a match further in opens eight tokens before it", 20, 8, true, true],
    ["a match near the end opens at the text's last 64 tokens", 100, 44, true, false],
  ])("%s", (_name, at, lead, cutBefore, cutAfter) => {
    const text = Array.from({ length: 120 }, (_, k) => (k === at ? "cache" : `w${k}`)).join(" ");
    const [hit] = searchFor("cache", [doc("d", "Notes")], "default", () => text).results;
    const before = hit.snippet.slice(0, hit.snippet.indexOf("[")).match(/[\p{L}\p{N}]+/gu) ?? [];
    expect([before.length, hit.snippet.startsWith("…"), hit.snippet.endsWith("…")]).toEqual([lead, cutBefore, cutAfter]);
  });
});
