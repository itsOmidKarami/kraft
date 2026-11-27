import type { EventType } from "../../../src/types/vocab.generated";
import { agentRun, DOCS_SCOPE, MODEL, PRICE, RUNS, taskRun, WAITS, type Run } from "./taskKinds";
import type { DefNode } from "./ngChain";

/**
 * How the walk runs a node, as a real server writes it: the sessions each task gets and the events on the way, in
 * order. One recipe, two users. The sample items (ngItems.ts) replay a node's beats at once, each write stamped by its
 * own clock; the demo's live motion (motion.ts) plays one beat a tick, through transitions.ts's doors. A beat is what
 * lies between two `yield`s: a session made and started, a session ended with what it reported, an event. Each user
 * hands the recipe a `Writer`, the writes it can make; the recipe never touches an item itself.
 */

export const TESTS = "verification.tests.test_changed_scopes";
export const REVIEW = "verification.review.code_review";
export const JUDGE = "verification.fix_loop.judge";
export const REPAIR = "verification.fix_loop.main.repair";
export const OPEN = "draft_merge_request.open.open";
/** A gate's reviewer, ready for a person: what it says on its way out. */
export const REVIEWED = "The document covers what the gate asks: nothing in it needs another pass before you decide.";
/** What a gate's self-pass says when the chain revision changed nothing (`chain_revision_unchanged`). */
const UNCHANGED = "The plan fits the chain as it stands: no node to add, drop or reorder.";

export type Open = { status?: string; round?: number; attempt?: number; thread?: number; run?: Run; noAgent?: boolean };

/** The writes a recipe makes. `dm` is the minutes the sample's clock moves first; the live walk reads the real one. */
export interface Writer {
  readonly id: string;
  /** The item's sessions, oldest first, the ones the recipe opens included. */
  readonly sessions: any[];
  readonly frozen: string;
  readonly repo: string;
  readonly head: string | null;
  /** The tasks its implementer reports (`plan_progress`), in order: none on a chain with no plan (ngContent.ts `planOf`). */
  readonly plan: readonly string[];
  /** The file verification's round adds (ngContent.ts, a `late` file): the one its first round of tests fails on. None for
   *  an item with no content of its own, whose round fails the sample's test. */
  readonly late?: string | null;
  /** The item's events, oldest first, the ones the recipe writes included. */
  readonly events: any[];
  /** How many polls an external wait takes, where the recipe does not say. */
  readonly polls?: number;
  /** How big the item's change is (ngContent.ts `sizeOf`): what its agents' tokens and time follow (`agentUsage`, `agentWall`). */
  readonly size: number;
  ev(type: EventType, payload?: Record<string, unknown>, dm?: number): void;
  /** `node_started`. */
  enter(node: string, dm: number): void;
  /** `store.create_session`, and an agent's launch: the session row. */
  open(path: string, node: string, o?: Open): any;
  /** `store.session_exited`: `status`, with the usage an agent reported. */
  close(s: any, status?: string, wall_ms?: number, advance?: boolean): any;
  /** A forge wait's poll that found it pending: the session `waiting`, the item waiting until `next`. */
  wait(s: any, node: string, next: string, wall_ms: number): void;
  /** `dm` minutes on from the clock, as an ISO instant. */
  at(dm: number): string;
  /** Move the sample's clock; nothing on the live one. */
  clock(dm: number): void;
  /** The head a fix cycle `k` left. */
  sha(k: number): string;
  requestGate(gate: string): void;
  /** A gate passed by Kraft itself (`gate_approved {by: "kraft"}`). */
  selfApprove(gate: string): void;
  mrOpened(node: string): void;
  complete(node: string): void;
  /** Whether a scope skip passed over this task or its step, or a step retry's fork started past it. */
  skipped(path: string): boolean;
  /** Whether a step retry's fork started past this task's step: its sessions from before are still this round's. */
  passed(path: string): boolean;
}

/** A recipe's beats; `T` is what it hands back once played out. */
export type Beats<T = void> = Generator<void, T, void>;
/** Every beat at once, as the sample items replay them; what the recipe hands back. */
export function drain<T>(g: Beats<T>): T {
  for (;;) {
    const r = g.next();
    if (r.done) return r.value;
  }
}

/** The cache reads that make up the rest of what `u` cost, at `MODEL`'s prices. */
export const cacheRead = (u: { cost_usd: number; tokens_in: number; tokens_cache_write: number; tokens_out: number }) =>
  Math.round((u.cost_usd * 1e6 - u.tokens_in * PRICE.tokens_in - u.tokens_cache_write * PRICE.tokens_cache_write - u.tokens_out * PRICE.tokens_out) / PRICE.tokens_cache_read);
/** What an agent's session reports on its way out, by its task's recipe (`agentRun`); `n` is its place among the item's
 *  sessions, which jitters it a little so no two read the same. Every kind of token, as an exit reports them
 *  (`usage.KINDS`): a little fresh input, a cache write as it first reads the repo, and the cache reads its cost
 *  leaves room for, most of an agent's prompt. */
export function agentUsage(hookPoint: string, n: number, size = 1) {
  const r = agentRun(hookPoint);
  const k = sized(hookPoint, size);
  const u = { model: MODEL, tokens_in: 40 + ((n * 37) % 300), tokens_cache_write: 18_000 + ((n * 211) % 9_000), tokens_cache_read: 0, tokens_out: Math.round(r.tokens_out * (1 + (n % 4) * 0.05) * k), cost_usd: Math.round(r.cost_usd * (1 + (n % 4) * 0.05) * k * 10_000) / 10_000 };
  return { ...u, tokens_cache_read: cacheRead(u) };
}
/** An agent's wall time by its recipe, jittered as `agentUsage` is, and sized as it is. */
export const agentWall = (hookPoint: string, n: number, size = 1) => Math.round(agentRun(hookPoint).wall_s * 1000 * (1 + (n % 3) * 0.07) * sized(hookPoint, size));
/** How much of an item's size (`Writer.size`, ngContent.ts `sizeOf`) a task's tokens and time follow: the implementer's
 *  all of it, a code review and the documents written about the change a little, the rest none (a spec or a plan is
 *  written from the brief). At size 1, an item with no content of its own, every task runs as its recipe says. */
const SIZED: Record<string, number> = { "implementation.main.implement": 1, [REVIEW]: 0.3, "work_brief.main.author": 0.15, "describe_merge_request.main.author": 0.15, "work_item_summary.main.author": 0.15 };
const sized = (hookPoint: string, size: number) => 1 + (SIZED[hookPoint] ?? 0) * (size - 1);

/** A finding as `findings_measured` lists it, and its fingerprint (`findings.Finding.fingerprint`, 16 hex). A failed scope runs as a plain
 *  subprocess and writes no findings file, so what reaches the round is a blind failure (`findings.from_blind_failure`): the hook, each
 *  failed command with an extract of its output, and the command to confirm the fix with; no file, no line, the hook as its source. */
export type Found = { finding: Record<string, unknown>; print: string; cmd?: string };
const blindFinding = (hook: string, cmd: string, extract: string) => ({ severity: "critical", message: `${hook} failed\n- ${cmd}: ${extract}\n\nConfirm the fix with: ${cmd}`, file: null, line: null, source_plugin: hook });
/** The line the repo's tests print for round 0's red test: the sample's, or the item's own, named after the file its
 *  round adds (a test file's test of its own name; a page's doctest, as pytest collects one). */
export function failedTest(late?: string | null): string {
  if (!late) return "FAILED tests/test_cache.py::test_cache_evicts_oldest - AssertionError";
  const base = late.split("/").pop()!.replace(/\.[^.]+$/, "").replace(/\.test$/, "");
  return /\.(md|rst|txt)$/.test(late) ? `FAILED ${late}::${late.split("/").pop()}` : `FAILED ${late}::${base} - AssertionError`;
}
/** The docs check's complaint about `page` (the sample's, `DOCS_SCOPE`'s, without one). */
const docsLine = (page = "docs/search.md") => `${page}:12 MD040/fenced-code-language Fenced code blocks should have a language specified`;
/** A failed scope's log as an item's own: its late file's red test, and its own page's docs complaint. */
export const ownFailure = (log: any[], late?: string | null, page?: string | null) =>
  log.map((l) => (late && l.text === failedTest() ? { ...l, text: failedTest(late) } : page && l.text === docsLine() ? { ...l, text: docsLine(page) } : l));
/** Round 0's red test, then round 1's red docs check: two defects, so two fingerprints, and the stuck detector does not fire. */
export const redOf = (late?: string | null): Found => ({ finding: blindFinding(TESTS, RUNS["kraft.verify_changed_test_scopes"].command!, failedTest(late)), print: "5be0c1d7a93e4f20", cmd: RUNS["kraft.verify_changed_test_scopes"].command });
export const docsRedOf = (page?: string | null): Found => ({ finding: blindFinding(TESTS, DOCS_SCOPE.command!, docsLine(page ?? undefined)), print: "c41f0e9b2d7a6853", cmd: DOCS_SCOPE.command });

/** A session that ran to `status`: made (and launched), a beat, then ended. */
export function* ran(w: Writer, path: string, node: string, status = "done", o: Open = {}): Beats<any> {
  const s = w.open(path, node, o);
  yield;
  return w.close(s, status);
}

/** One changed-test-scope pass: what it picked, then a session per scope command, the repo's tests failing when `red`
 *  and the docs check when `docsRed`. */
export function* testPass(w: Writer, round: number, red = false, docsRed = false): Beats {
  w.ev("test_scopes_selected", { node_id: "verification", hook_point: TESTS, repository: null, round, commands: [RUNS["kraft.verify_changed_test_scopes"].command, DOCS_SCOPE.command], targeted: {}, full_reason: null }, 0.05);
  yield* ran(w, TESTS, "verification", red ? "failed" : "done", { round });
  yield* ran(w, TESTS, "verification", docsRed ? "failed" : "done", { round, run: DOCS_SCOPE });
}
/** A finding as the walk writes it (`asdict(Finding)`): `same_as`, and the jobs a blind failure points at, each its failed session's log (`findings.session_log_ref`). */
const asFinding = (w: Writer, f: Found) => {
  const job = [...w.sessions].reverse().find((x) => x.hook_point === f.finding.source_plugin && x.status !== "done" && (f.cmd ? x.command === f.cmd : !x.command));
  return { ...f.finding, same_as: null, jobs: job ? [{ label: f.cmd ?? String(f.finding.source_plugin), log_ref: `kraft view logs ${w.id} --session ${job.id}` }] : [] };
};
/** `walk`'s `findings_measured` for round `round`: which measuring tasks had a session that round (a failed step's later
 *  ones have none), the findings, and their fingerprints. Its head is the round's last session's. */
export const measured = (w: Writer, round: number, found: Found[] = [], had = [TESTS, REVIEW]) => w.ev("findings_measured", {
  node_id: "verification", cycle: round, head_sha: w.sessions.at(-1).head_sha,
  measured_tasks: [...had].sort(), unmeasured_tasks: [TESTS, REVIEW].filter((p) => !had.includes(p)).sort(),
  findings: found.map((f) => asFinding(w, f)), fingerprints: found.map((f) => f.print).sort(),
}, 0.05);
/** A fix cycle (`walk`): its start, the repair, and its finish at the head the repair left, never null: one that
 *  committed nothing finishes at the head it started from. */
export function* fixCycle(w: Writer, cycle: number, committed: boolean): Beats {
  w.ev("fix_cycle_started", { node_id: "verification", cycle, max_attempts: 2, failed_tasks: [TESTS] }, 0.05);
  yield* ran(w, REPAIR, "verification", "done", { round: cycle, attempt: cycle });
  w.ev("fix_cycle_finished", { node_id: "verification", cycle, committed, head_sha: w.sha(committed ? cycle : cycle - 1) }, 0.02);
}
/** The fix loop's judge, due only once a repair has run (`walk.judge_due`): it reads round `round`'s measurement before
 *  the next cycle starts, and its verdict carries the findings it judged. */
export function* judge(w: Writer, round: number, found: Found[], reasoning: string): Beats {
  yield* ran(w, JUDGE, "verification", "done", { round });
  w.ev("judge_verdict", { node_id: "verification", cycle: round, verdict: "continue", reasoning, findings: found.map((f) => asFinding(w, f)) }, 0.02);
}
/** A forge wait's bounds in seconds, as `ForgeTask.wait_bounds` reads them (a task's `total_time_cap_minutes` and `wait.polling`,
 *  `DEFAULT_WAIT` for what it leaves out; `config/library.yaml`): its timeout, its first interval and its longest. */
const BOUNDS: Record<string, { timeout: number; initial: number; max: number }> = {
  "mr.ci": { timeout: 5_400, initial: 30, max: 300 },
  "mr.post_merge_ci": { timeout: 5_400, initial: 30, max: 300 },
  "mr.merge": { timeout: 5_400, initial: 30, max: 300 },
  "mr.automated_review": { timeout: 1_800, initial: 30, max: 300 },
  "mr.external_approval": { timeout: 604_800, initial: 300, max: 3_600 },
};
/** `waits.open_wait`: how many looks `task`'s open wait has had, or null when none is open (its last one ended, or a
 *  fork restarted the run since). */
export const openWait = (w: Pick<Writer, "events">, task: string): number | null => {
  // `waits.open_wait` cuts at every restart of the run, not only a fork.
  const evs = w.events.slice(Math.max(...["work_item_retried", "run_forked", "base_change_restart", "merge_rejection_restart"].map((t) => w.events.map((e) => e.type).lastIndexOf(t))) + 1);
  let seen = 0;
  for (const e of [...evs].reverse()) {
    if (e.payload?.task !== task) continue;
    if (e.type === "external_wait_observed") seen++;
    else if (e.type === "external_wait_started") return seen;
    else if (e.type === "external_wait_ended") return null;
  }
  return null;
};
/** A forge wait (`waits.py`): one session that goes `waiting` poll after poll, a fresh `node_started` each time its
 *  poller re-enters it; settled, it ends done. Unsettled, it is left waiting until `retryAt`. Each poll after the first
 *  is a beat of its own: the item waits between them. One that settles at its first look ends a beat after its session
 *  is made, as `ran`'s does. A session of this node's left `waiting` by an earlier look (a pause stops only live ones)
 *  is reused (`store.create_session`'s `reuse_if_waiting`) and its open wait goes on: no second start, its looks
 *  numbered on. `polls` is the looks this play takes, one at least. */
export function* awaitPolls(w: Writer, node: string, path: string, kind: string, polls: number, settled: boolean, retryAt?: string): Beats<any> {
  const reused = [...w.sessions].reverse().find((x) => x.node_id === node && x.hook_point === path && x.round === 0 && x.status === "waiting");
  const s = reused ?? w.open(path, node);
  const had = openWait(w, path);
  const first = (had ?? 0) + 1;
  const last = Math.max(polls, first);
  if (!reused && settled && first === last) yield;
  // The demo's repos configure no review bot: the review wait's look says so before it settles (`forge.run`'s collect path).
  if (kind === "mr.automated_review") w.ev("automated_review_not_configured", { node_id: node, task: path, repo: w.repo }, 0.02);
  // What it observes (`_WAITS`' condition): its target's own name.
  const condition = kind.replace(/^mr\./, "");
  const bounds = BOUNDS[kind] ?? BOUNDS["mr.ci"];
  const each = (RUNS[taskRun(w.frozen, path) ?? ""]?.wall_ms ?? 60_000) / (last - first + 1);
  if (had === null) {
    // `waits.observe`'s start of a wait: its bounds and deadline, no session (its end names that).
    const started_at = w.at(0);
    w.ev("external_wait_started", { task: path, node_id: node, kind, timeout_s: bounds.timeout, initial_interval_s: bounds.initial, max_interval_s: bounds.max, started_at, deadline: new Date(Date.parse(started_at) + bounds.timeout * 1000).toISOString() }, 0.02);
  }
  let wall = 0;
  // The first look of this play follows the `node_started` that entered the node; each later one, its poller's.
  for (let k = first; k <= last; k++) {
    if (k > first) {
      yield;
      w.enter(node, 0.02);
    }
    wall += each;
    w.clock(each / 60_000);
    if (settled && k === last) {
      w.ev("external_wait_observed", { node_id: node, task: path, observation: k, condition, state: "settled", result: "done" }, 0);
      w.ev("external_wait_ended", { node_id: node, task: path, outcome: "settled", result: "done", observations: k, session_id: s.id }, 0);
      return w.close(s, "done", Math.round(wall), false);
    }
    // `WaitBounds.interval_after`, in minutes: 30 s after the first pending look, doubling up to the 300 s most.
    const gap = Math.min((bounds.initial / 60) * 2 ** (k - 1), bounds.max / 60);
    const next = k === last && retryAt ? retryAt : w.at(gap);
    w.ev("external_wait_observed", { node_id: node, task: path, observation: k, condition, state: "pending", result: "pending", next_observation_at: next }, 0);
    w.wait(s, node, next, Math.round(wall));
    if (k < last) w.clock(gap);
  }
  return s;
}

/** The plan's tasks of an item with no content of its own (the item page's samples), as its implementer reports them. */
export const PLAN = ["Read the search path", "Key the cache by content hash", "Invalidate on reindex", "Bound the cache", "Tests", "Docs"];

/** A gate the walk reaches: asked for, its reviewer run and ready for a person (`gate_review`), who decides it. The
 *  person's decision is not the recipe's. `chain_revision_approval` passes itself when the revision changed nothing
 *  (`chain_revision_unchanged`), with no request. */
function* gateBeats(w: Writer, n: DefNode): Beats {
  w.enter(n.id, 0.2);
  if (n.id === "chain_revision_approval") {
    w.ev("chain_revision_unchanged", { gate: n.id, rationale: UNCHANGED }, 0.02);
    w.selfApprove(n.id);
    w.complete(n.id);
    return;
  }
  w.requestGate(n.id);
  yield;
  yield* ran(w, `${n.id}.auto_review`, n.id);
  w.ev("gate_auto_review_skipped", { gate: n.id, reason: "passed", note: REVIEWED }, 0.02);
}

/** A node the walk runs to its end: `node_started`, its tasks in order (less those a scope skip passed over), and
 *  `node_completed`; a gate up to its request. `red`: verification's first round of tests fails, and a fix cycle mends it. */
export function* nodeBeats(w: Writer, n: DefNode, o: { red?: boolean } = {}): Beats {
  if (n.kind === "gate") return yield* gateBeats(w, n);
  w.enter(n.id, 0.2);
  const go = (path: string) => !w.skipped(path);
  switch (n.id) {
    case "implementation": {
      if (!go("implementation.main.implement")) break;
      const s = w.open("implementation.main.implement", n.id);
      // Its plan's tasks as it reports them, half of them a beat; with no plan it reports none, and works two beats all the same.
      // A minute and a half apart, closer in a run too short for that: every report falls inside it.
      const half = Math.ceil(w.plan.length / 2);
      const gap = Math.min(1.5, agentWall(s.hook_point, w.sessions.indexOf(s), w.size) / 60_000 / (w.plan.length + 1));
      yield;
      for (let k = 0; k < w.plan.length; k++) {
        if (k === half) yield;
        w.ev("plan_progress", { node_id: n.id, task: k + 1, total: w.plan.length, title: w.plan[k] }, gap);
      }
      if (!w.plan.length) yield;
      w.clock(-gap * w.plan.length);
      w.close(s);
      break;
    }
    case "verification": {
      // `collect_findings` reads the round's rows, so a step a step retry passed still counts as measured.
      const had = [TESTS, REVIEW].filter((p) => go(p) || w.passed(p));
      let round = 0;
      if (go(TESTS) && o.red) {
        // The tests step failed, so the review step after it never ran this round; a fix cycle mends it.
        yield* testPass(w, 0, true);
        measured(w, 0, [redOf(w.late)], [TESTS]);
        yield* fixCycle(w, 1, true);
        round = 1;
      }
      if (go(TESTS)) yield* testPass(w, round);
      if (go(REVIEW)) yield* ran(w, REVIEW, n.id, "done", round ? { round } : {});
      if (had.length) measured(w, round, [], had);
      break;
    }
    case "draft_merge_request": {
      if (go("draft_merge_request.rebase.rebase")) yield* ran(w, "draft_merge_request.rebase.rebase", n.id);
      if (!go(OPEN)) break;
      // The open task pushes the branch and opens the merge request inside its own session (`forge.run`).
      const s = w.open(OPEN, n.id);
      w.ev("branch_pushed", { node_id: n.id, repo: w.repo, sha: w.head }, 0.02);
      w.mrOpened(n.id);
      yield;
      w.close(s);
      break;
    }
    case "merge_request_feedback":
      if (go("merge_request_feedback.ci.await_ci")) yield* awaitPolls(w, n.id, "merge_request_feedback.ci.await_ci", "mr.ci", w.polls ?? 3, true);
      if (go("merge_request_feedback.automated_review.await_review")) yield* awaitPolls(w, n.id, "merge_request_feedback.automated_review.await_review", "mr.automated_review", 1, true);
      if (go("merge_request_feedback.review_feedback.triage")) yield* ran(w, "merge_request_feedback.review_feedback.triage", n.id, "done", { noAgent: true });
      break;
    case "post_merge_ci":
      if (go("post_merge_ci.main.await")) yield* awaitPolls(w, n.id, "post_merge_ci.main.await", "mr.post_merge_ci", w.polls ?? 4, true);
      break;
    default:
      // A node of one task, or one per step: each runs in turn, a forge wait (external_approval's, merge's) as one.
      for (const path of n.tasks as string[]) {
        const run = taskRun(w.frozen, path) ?? "";
        if (go(path)) yield* WAITS.has(run) ? awaitPolls(w, n.id, path, run, 1, true) : ran(w, path, n.id);
      }
  }
  w.complete(n.id);
}
