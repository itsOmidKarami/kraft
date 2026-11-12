// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { buildScenario, withScopes, type ItemBundle, type Scenario } from "../e2e/contract/mocks/fixtures";
import { FRESH_TITLES, motion } from "../e2e/contract/mocks/motion";
import { frozenOf } from "../e2e/contract/mocks/ngChain";
import { AUTO_ESCALATE_MESSAGE, MR_CLOSED_REASON, NG_NOW } from "../e2e/contract/mocks/ngItems";
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

/** The node whose completion files each gate document, and the document's kind (`produces`): spec iff past `spec`, plan
 *  iff past `plan`, the chain revision past `chain_revision`, the work brief past `work_brief`, the review brief past
 *  the summary node. Written out, not read off the mock. */
const PRODUCED: Record<string, string> = { spec: "spec", plan: "plan", chain_revision: "chain_revision", work_brief: "work_brief", work_item_summary: "review_brief" };
const completed = (b: ItemBundle, node: string) => b.events.some((e) => e.type === "node_completed" && e.payload.node_id === node);
const ENDED_STATUSES = ["completed", "abandoned"];

describe.each<[string, () => Scenario]>([["the default sample", () => buildScenario("default")], ["the live walk", walked]])("each board item's own content in %s", (_scenario, build) => {
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
    return d.status === 200 ? [...d.body.landed.files, ...d.body.files] : [];
  };
  const doneAgent = (s: any) => s.session_summary_ref && ["done", "done_with_concerns"].includes(s.status);

  it("every board item has a brief, and no two share one", () => {
    const briefs = board.map((b) => b.item.description);
    expect(board.filter((b) => !b.item.description).map((b) => b.item.title)).toEqual([]);
    expect(briefs.filter((x, k) => briefs.indexOf(x) !== k)).toEqual([]);
  });
  // The strict rule: every entry of the content table is written for its own item, so no list may repeat, though two
  // rows of one kind in one repo might in principle change the same files.
  it("no two board items that have changed something share the list of files they changed", async () => {
    const lists = (await Promise.all(board.map(served))).map((fs) => fs.map((f) => f.path).sort().join(" ")).filter(Boolean);
    expect(lists.filter((x, k) => lists.indexOf(x) !== k)).toEqual([]);
  });
  it("an item that has not completed implementation has changed nothing", async () => {
    const wrong = await Promise.all(board.filter((b) => !completed(b, "implementation")).map(async (b) => [b.item.title, (await served(b)).length] as const));
    expect(wrong.filter(([, n]) => n > 0)).toEqual([]);
  });
  it("a docs item changes docs only", () => {
    const docs = board.filter((b) => b.item.repo.endsWith("/kraft-docs") || b.item.chain_template === "docs_only");
    expect(docs.length).toBeGreaterThan(0);
    expect(docs.flatMap((b) => b.content!.files.map(([p]) => p).filter((p) => !p.startsWith("docs/") && !p.endsWith(".md")))).toEqual([]);
  });
  it("its header's stat is its files': the implementation's once that completed, and verification's once that did", async () => {
    const wrong = await Promise.all(board.filter((b) => b.item.worktree_exists !== false && b.events.some((e) => e.type === "node_started")).map(async (b) => {
      const want = completed(b, "implementation") ? b.content!.files.filter((f) => !f[3] || completed(b, "verification")) : [];
      const got = await served(b);
      const sum = (fs: { insertions: number; deletions: number }[]) => [fs.length, fs.reduce((a, f) => a + f.insertions, 0), fs.reduce((a, f) => a + f.deletions, 0)];
      const compare = (await call(`/work-items/${b.item.id}/compare`)).body.files.map((f: any) => f.path).sort();
      const ok = JSON.stringify(sum(got)) === JSON.stringify(sum(want.map(([, insertions, deletions]) => ({ insertions, deletions })))) && JSON.stringify(compare) === JSON.stringify(want.map(([p]) => p).sort());
      return ok ? null : b.item.title;
    }));
    expect(wrong.filter(Boolean)).toEqual([]);
  });
  it("it has a merge request once its draft's open task has run, its own, the one its mr_opened named", () => {
    const wrong = board.flatMap((b) => {
      const opened = b.sessions.some((s) => s.hook_point === "draft_merge_request.open.open" && s.status === "done");
      const event = b.events.filter((e) => e.type === "mr_opened").at(-1)?.payload;
      const mr = b.item.mr_ref;
      const own = { number: b.content!.mr, url: `https://github.com/acme/${b.item.repo.split("/").pop()}/pull/${b.content!.mr}` };
      return opened !== !!mr || (mr && (JSON.stringify(mr) !== JSON.stringify(own) || event?.number !== mr.number || event?.url !== mr.url)) ? [b.item.title] : [];
    });
    expect(wrong).toEqual([]);
  });
  it("no two documents share an id, on any item", () => {
    const ids = Object.values(S.docs).flat().map((d) => d.document_id);
    expect(ids.filter((x, k) => ids.indexOf(x) !== k)).toEqual([]);
  });
  it("its documents are the gate documents of the nodes it completed and its finished agent sessions' summaries, in its own folder", () => {
    const wrong = board.flatMap((b) => {
      const id = b.item.id;
      const want = [
        ...Object.entries(PRODUCED).filter(([node]) => completed(b, node)).map(([, kind]) => `items/${id}/docs/${kind}.md`),
        ...new Set(b.sessions.filter(doneAgent).map((s) => s.session_summary_ref)),
      ].sort();
      const got = (S.docs[id] ?? []).map((d) => d.path).sort();
      return JSON.stringify(got) !== JSON.stringify(want) || got.some((p) => !p.startsWith(`items/${id}/`)) ? [`${b.item.title}: ${got.length} of ${want.length}`] : [];
    });
    expect(wrong).toEqual([]);
  });
  /** Whether a document's text is about `b`: it names its title and says what its brief's first sentence says. */
  const about = (text: string, b: ItemBundle) => text.includes(b.item.title) && text.includes(b.item.description.split(". ")[0]);
  // An escalation reply the walk's agent wrote (`docText`) has its own words and no front matter; motion.test.ts holds it.
  it("each document opens to its own item and is about it", async () => {
    const wrong: string[] = [];
    for (const b of board) for (const d of (S.docs[b.item.id] ?? []).filter((x) => S.docText?.[x.document_id] == null)) {
      const doc = (await call(`/documents/${d.document_id}`)).body;
      if (doc.id !== d.document_id || !doc.metadata.work_item_ids.includes(b.item.id) || !about(doc.content, b)) wrong.push(`${b.item.title}: ${d.path}`);
    }
    expect(wrong).toEqual([]);
  });
  it("a gate's document is about its item", async () => {
    const gated = board.filter((b) => b.item.pending_gate && b.item.gate_artifact);
    expect(S === walk || gated.length > 0).toBe(true);
    const wrong = await Promise.all(gated.map(async (b) => (about((await call(`/work-items/${b.item.id}/artifact`)).body.content, b) ? null : b.item.title)));
    expect(wrong.filter(Boolean)).toEqual([]);
  });
  it("search finds nothing for a query nothing holds, and only board items' documents for one many do", async () => {
    expect((await call("/search?q=zzzqx")).body.results).toEqual([]);
    const ids = new Set(board.map((b) => b.item.id));
    const hits = (await call("/search?q=session")).body.results;
    expect(hits.length).toBeGreaterThan(0);
    expect(hits.filter((r: any) => !ids.has(r.links[0].work_item_id)).map((r: any) => r.title)).toEqual([]);
  });
  it("search for an item's title puts that item's documents first", async () => {
    const wrong = await Promise.all(board.filter((b) => S.docs[b.item.id]?.length).map(async (b) => {
      const hits = (await call(`/search?q=${encodeURIComponent(b.item.title)}`)).body.results;
      const first = hits.slice(0, Math.min(6, S.docs[b.item.id].length));
      return first.length && first.every((r: any) => r.links[0].work_item_id === b.item.id) ? null : b.item.title;
    }));
    expect(wrong.filter(Boolean)).toEqual([]);
  });
  it("it has run from its first node to the clock, or to its last event once it ended", () => {
    const wrong = board.filter((b) => b.events.some((e) => e.type === "node_started")).flatMap((b) => {
      const it = b.item;
      const first = b.events.find((e) => e.type === "node_started")!.created_at;
      const ended = ENDED_STATUSES.includes(it.status);
      return (it.started_at ?? it.created_at) !== first || (ended && (it.ended_at ?? it.updated_at) !== b.events.at(-1)!.created_at) ? [it.title] : [];
    });
    expect(wrong).toEqual([]);
  });
  it("no two ended board items ran for the same time", () => {
    const took = board.filter((b) => ENDED_STATUSES.includes(b.item.status) && b.item.started_at).map((b) => Date.parse(b.item.ended_at ?? b.item.updated_at) - Date.parse(b.item.started_at));
    expect(took.length).toBeGreaterThan(1);
    expect(took.filter((x, k) => took.indexOf(x) !== k)).toEqual([]);
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

describe("the live walk runs each node as the sample's finished item ran it", () => {
  const S = walked();
  const fresh = (n: number) => S.bundles[S.ngBoard.find((id) => S.bundles[id].item.title === FRESH_TITLES[n])!];
  const sample = byNode(S.bundles[S.ng.done]);
  it("walks both items it filed to the end of the default chain", () => {
    expect([0, 1].map((n) => [fresh(n).item.status, fresh(n).item.chain_template, [...byNode(fresh(n)).keys()]])).toEqual([0, 1].map(() => ["completed", "default", CHAIN_NODES.default]));
  });
  // The seeds and the walk read one recipe (nodeRecipe.ts); this holds what each user does with it to the same events.
  it.each(CHAIN_NODES.default)("%s", (node) => {
    expect(sample.get(node)).toBeDefined();
    expect(byNode(fresh(1)).get(node)).toEqual(sample.get(node));
    if (node !== "verification") expect(byNode(fresh(0)).get(node)).toEqual(sample.get(node));
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
/** `ci` and `post` are how many looks the CI and post-merge CI waits take. */
const NODE_EVENTS: Record<string, (ci: number, post: number) => string[]> = {
  spec: () => ["node_started", ...AGENT, "node_completed"],
  spec_approval: () => GATE,
  plan: () => ["node_started", ...AGENT, "node_completed"],
  plan_approval: () => GATE,
  chain_revision: () => ["node_started", ...AGENT, "node_completed"],
  // It changed nothing, so its gate passes itself (`chain_revision_unchanged`), with no request.
  chain_revision_approval: () => ["node_started", "chain_revision_unchanged", "gate_approved", "node_completed"],
  implementation: () => ["node_started", "worker_session_created", "worker_session_started", ...Array(6).fill("plan_progress"), "worker_session_exited@null", "node_completed"],
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
  // written here as numbers so a change to either fails.
  it.each(CHAIN_NODES.default)("%s", (node) => {
    expect(segments(S.bundles[S.ng.done]).get(node)).toEqual(NODE_EVENTS[node](3, 4));
    expect(segments(fresh(1)).get(node)).toEqual(NODE_EVENTS[node](2, 2));
    expect(segments(fresh(0)).get(node)).toEqual(node === "verification" ? RED_VERIFICATION : NODE_EVENTS[node](2, 2));
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
