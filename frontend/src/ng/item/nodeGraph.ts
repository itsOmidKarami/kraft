import { elapsed, elapsedBetween } from "../../format";
import type { ChainNode as ApiNode, KraftEvent, SessionStatus, WorkerSession } from "../../types";
import type { NodeStep } from "../graph/nodeLayout";
import type { GlyphState, GraphItem } from "../graph/types";
import { attemptsAt, loopPaths, materialized, taskKindAt } from "./chainValues";
import { stepsOf, taskName } from "./paths";
import { isScopeTask } from "./scopeView";
import type { ItemDetail } from "./useItem";

export const ESCALATION = "escalation";
/** A gate's reviewer: its sessions run at `<gate>.auto_review`, a path no step of the chain lists. */
export const AUTO_REVIEW = "auto_review";
export const isEscalation = (s: WorkerSession) => s.hook_point === ESCALATION || s.hook_point.endsWith(`.${ESCALATION}`);
// What the graph counts as in flight; not `LIVE_SESSION_STATUSES` (pending and running only).
const LIVE = new Set<SessionStatus>(["running", "pending", "rate_limited", "waiting", "needs_context"]);

/** A node's sessions in the pass it is on, oldest first, each with the round it reads in. A retry or a base change
 *  restarts a fix loop at round 0, so a measurement below the highest one seen starts a new pass (`node_started` fires
 *  per measurement and cannot tell). The loop's own repair and judge never count: a fix cycle that was paused and
 *  refunded is measured again at a round below the repair it follows. A negative round (the re-measure after `on_failure`,
 *  `walk._REPAIR_ROUND`) starts nothing and reads in the round of the session before it. */
export function passOf(item: ItemDetail, node: string): WorkerSession[] {
  const all = item.worker_sessions.filter((s) => s.node_id === node && !isEscalation(s)).sort((a, b) => a.created_at.localeCompare(b.created_at));
  let from = 0;
  let top = 0;
  let last = 0;
  const read = all.map((s, i) => {
    if (s.round >= 0) {
      last = s.round;
      if (!s.hook_point.startsWith(`${node}.${FIX_LOOP}.`)) {
        if (s.round < top) from = i;
        top = s.round;
      }
    }
    return s.round < 0 ? { ...s, round: last } : s;
  });
  return read.slice(from);
}

/** A task path's sessions in the node's current pass, attempt order (the attempt switcher's list). */
export function sessionsOf(item: ItemDetail, path: string): WorkerSession[] {
  const all = item.worker_sessions.filter((s) => s.hook_point === path);
  const pass = all[0] && !isEscalation(all[0]) ? new Map(passOf(item, all[0].node_id).map((s) => [s.id, s])) : undefined;
  return all.flatMap((s) => (pass ? (pass.get(s.id) ?? []) : s)).sort((a, b) => a.attempt - b.attempt || a.created_at.localeCompare(b.created_at));
}
/** The node's escalation turns, oldest first. */
export const escalationsOf = (item: ItemDetail, node: string) => item.worker_sessions.filter((s) => s.node_id === node && isEscalation(s)).sort((a, b) => a.created_at.localeCompare(b.created_at));

/** How many of an item's escalation messages, oldest first, a thread shows for
 *  the turn `upTo`: through that turn's own message, and all of them when no
 *  turn is picked. A message names the session it started; one that does not
 *  is matched by the turn's place among its node's turns and messages. */
export function messagesThrough(msgs: { session: string | null; node: string | null }[], upTo: WorkerSession | undefined, item: ItemDetail): number {
  if (!upTo) return msgs.length;
  const own = msgs.findIndex((m) => m.session === upTo.id);
  if (own >= 0) return own + 1;
  const k = escalationsOf(item, upTo.node_id).findIndex((s) => s.id === upTo.id);
  const here = msgs.flatMap((m, i) => (m.node === upTo.node_id ? [i] : []));
  return k >= 0 && k < here.length ? here[k] + 1 : msgs.length;
}

type Look = Pick<GraphItem, "state" | "meta" | "running" | "paused" | "attemptStopped">;
const FAILED_LOOK = (): Look => ({ state: "failed" });
const DONE_LOOK = (s: WorkerSession): Look => ({ state: "done", meta: s.wall_ms != null ? elapsed(s.wall_ms) : undefined });
const LOOK: Record<SessionStatus, (s: WorkerSession, now: number) => Look> = {
  pending: () => ({ state: "todo", meta: "pending" }),
  running: (s, now) => ({ state: "current", running: true, meta: `running · ${elapsedBetween(s.started_at, null, now)}` }),
  paused: () => ({ state: "current", paused: true, meta: "paused" }),
  rate_limited: () => ({ state: "amber", meta: "waiting" }),
  waiting: () => ({ state: "amber", meta: "waiting" }),
  needs_context: () => ({ state: "current", meta: "needs you" }),
  failed: FAILED_LOOK, config_error: FAILED_LOOK, unknown: FAILED_LOOK,
  conflict: FAILED_LOOK, infra: FAILED_LOOK, infra_stop: FAILED_LOOK,
  capped_out: () => ({ state: "failed", attemptStopped: true }),
  done: DONE_LOOK, done_with_concerns: DONE_LOOK,
};

/** A task a person skipped: the walk counts it done, and it says why it did not finish. */
const SKIPPED: Look = { state: "done", meta: "skipped" };
/** Whether `path` is, or is under, a task or step skipped in this run. */
const skippedAt = (item: ItemDetail, path: string) => !!item.skipped_paths?.some((p) => path === p || path.startsWith(`${p}.`));

/** Whether a task's newest session leaves it settled: it finished, or a person skipped it. */
export const settled = (s: WorkerSession | undefined) => !!s && (!!s.skipped || s.status.startsWith("done"));
/** A session's status as a fact row's words. */
export const statusWords = (s: WorkerSession) => (s.skipped ? "skipped" : s.status.replaceAll("_", " "));

/** What a session draws as: its glyph state and the one meta word (Decisions §5 Task meta: duration only). */
export function sessionLook(s: WorkerSession | undefined, now: number): Look {
  if (!s) return { state: "todo" };
  if (s.skipped) return SKIPPED;
  return (LOOK[s.status] ?? DONE_LOOK)(s, now); // a status from a newer server reads as done, as before
}

/** The step a fix loop's own tasks are selected under: `<node>.fix_loop.<task>`. */
export const FIX_LOOP = "fix_loop";
export const JUDGE = "judge";

/** A node's fix-loop rounds, 1-based. A round is one measurement of the node's own steps
 *  (`WorkerSession.round`, 0-based). `total` is the loop's limit, its attempts plus the first pass,
 *  when the chain says; the judge's session carries the round it came after, the repair's the
 *  round it leads into. */
export function loopRounds(item: ItemDetail, node: ApiNode): { latest: number; total?: number } | undefined {
  if (!node.fix_loop) return;
  const own = new Set(stepsOf(node).steps.flatMap((st) => st.tasks));
  const ran = passOf(item, node.id).filter((s) => own.has(s.hook_point)).map((s) => s.round + 1);
  if (!ran.length) return;
  const m = materialized(item);
  const max = m ? Number(attemptsAt(m, node.id, item.policy_override)?.value) : NaN;
  return { latest: Math.max(...ran), total: Number.isFinite(max) ? max + 1 : undefined };
}

/** The round a node's canvas and pane show: the one picked when it ran, else the newest. */
export const roundShown = (item: ItemDetail, node: ApiNode, picked?: number): number | undefined => {
  const r = loopRounds(item, node);
  return r && (picked && picked <= r.latest ? picked : r.latest);
};

export const verdictOf = (events: KraftEvent[] | undefined, node: string, cycle: number) =>
  events?.filter((e) => e.type === "judge_verdict" && e.payload.node_id === node && e.payload.cycle === cycle).at(-1)?.payload.verdict as string | undefined;

/** A node's inside for NodeGraph (Decisions §7): steps in order, a row per
 *  task with its state in the round shown (a fix-loop node's own round: every
 *  task, its repair and its judge follow it), the escalation side branch, the
 *  fix-loop arc once a round ran, and the on_failure footer line. */
export function nodeGraph(item: ItemDetail, node: ApiNode, now = Date.now(), events?: KraftEvent[], picked?: number) {
  const { steps } = stepsOf(node);
  const frozen = materialized(item);
  const rounds = loopRounds(item, node);
  const shown = roundShown(item, node, picked);
  const out: NodeStep[] = steps.map((st) => ({
    id: st.id,
    tasks: st.tasks.map((path): GraphItem => {
      const ss = sessionsOf(item, path);
      // In a fix-loop node the round says which run this is; a count of runs across rounds would badge every task.
      const last = shown ? ss.filter((s) => s.round === shown - 1).at(-1) : ss.at(-1);
      // The scopes of a changed-test-scope task are sessions of it too, but not attempts: no count on its box.
      return { id: taskName(path), taskKind: taskKindAt(frozen, path) ?? (ss.some((s) => s.model) ? "agent" : undefined), attempt: shown || isScopeTask(item, path) ? undefined : last?.attempt, ...(!last && skippedAt(item, path) ? SKIPPED : sessionLook(last, now)) };
    }),
  }));
  const esc = escalationsOf(item, node.id);
  const lastEsc = esc.at(-1);
  const side: GraphItem | undefined = lastEsc && { id: ESCALATION, icon: "siren", ...sessionLook(lastEsc, now), meta: `thread ${lastEsc.thread} · turn ${esc.filter((s) => s.thread === lastEsc.thread).length}` };
  const stopped = item.stop?.node === node.id && (item.stop.kind === "cap" || item.stop.kind === "budget");
  const own = loopPaths(frozen, node.id);
  const inLoop = passOf(item, node.id).some((s) => s.hook_point.startsWith(`${node.id}.${FIX_LOOP}.`));
  // The arc is drawn once the loop did something: a second round, or a repair or judge that ran.
  // Red while the newest round is shown and the loop stopped; amber while it runs on past its first round.
  const tone = stopped && shown === rounds?.latest ? "red" : rounds && rounds.latest > 1 && item.current_node_id === node.id ? "active" : "idle";
  const loop = node.fix_loop && rounds && (rounds.latest > 1 || inLoop) ? loopOf(item, node, own, shown!, rounds, tone, now, events) : undefined;
  const onFailure = node.on_failure?.length ? node.on_failure.map(taskName).join(", ") : undefined;
  return { steps: out, side, loop, onFailure, rounds: rounds && shown ? roundList(item, node, rounds, shown) : undefined };
}

/** Why a round has no repair after it (yet). */
export const repairIdle = (round: number, total: number | undefined, judgeStopped: boolean | undefined) =>
  judgeStopped ? "stopped by judge" : round >= (total ?? Infinity) ? "last round" : "not yet";

type Loop = { tone: "idle" | "active" | "red"; label: string; tasks: GraphItem[] };

/** The arc: its label, and the repair and judge for the round shown (the judge last). */
function loopOf(item: ItemDetail, node: ApiNode, own: ReturnType<typeof loopPaths>, round: number, rounds: { latest: number; total?: number }, tone: Loop["tone"], now: number, events?: KraftEvent[]): Loop {
  const frozen = materialized(item);
  const verdict = verdictOf(events, node.id, round - 1);
  const judgeStop = verdict?.startsWith("stop");
  const task = (path: string, run: WorkerSession | undefined, idle: string, over: Partial<GraphItem> = {}): GraphItem => ({
    // Selected as `<step>.<task>` under `fix_loop`, drawn as the task's own name.
    id: path.split(".").slice(2).join("."),
    label: taskName(path),
    taskKind: taskKindAt(frozen, path) ?? (run?.model ? "agent" : undefined),
    ...(run ? sessionLook(run, now) : { state: "todo" as const, meta: idle, faded: true }),
    ...over,
  });
  // The repair that went between this round and the next, stamped with the next one's index.
  const repair = own.repair.map((path) => {
    const run = sessionsOf(item, path).filter((s) => s.round === round).at(-1);
    const g = task(path, run, repairIdle(round, rounds.total, judgeStop));
    // The prototype says what became of it: "done · 5m".
    return run?.status === "done" && g.meta ? { ...g, meta: `done · ${g.meta}` } : g;
  });
  const tasks = [...repair];
  if (own.judge) {
    const run = sessionsOf(item, own.judge).filter((s) => s.round === round - 1).at(-1);
    const done = run?.status === "done";
    // The judge draws as the scales, as the prototype has it; the repair keeps its task kind's.
    tasks.push(task(own.judge, run, round === 1 ? "skipped · first repair" : "not yet", { icon: "scale", ...(done && verdict ? { meta: judgeStop ? "stop" : verdict.split("_")[0], ...(judgeStop && { state: "failed" as const }) } : {}) }));
  }
  return { tone, tasks, label: `round ${round}${rounds.total ? ` of ${rounds.total}` : ""}` };
}

export type RoundRow = { n: number; tone: "ok" | "warn" | "bad"; outcome: string };
export type Rounds = { rows: RoundRow[]; selected: number; latest: number; total?: number };

/** One row per round that ran, oldest first: its dot and what became of it. A round the loop moved on from was
 *  sent to the fix loop (red); the newest is stopped (red) when the item stopped on the node, running (amber)
 *  while the node is the one the run stands on, else done (green). */
function roundList(item: ItemDetail, node: ApiNode, { latest, total }: { latest: number; total?: number }, selected: number): Rounds {
  const halted = item.stop?.node === node.id;
  const rows = Array.from({ length: latest }, (_, i): RoundRow => {
    const n = i + 1;
    if (n < latest) return { n, tone: "bad", outcome: "sent to the fix loop" };
    if (halted) return { n, tone: "bad", outcome: "stopped · needs you" };
    if (item.current_node_id === node.id) return { n, tone: "warn", outcome: "running" };
    return { n, tone: "ok", outcome: "done" };
  });
  return { rows, selected, latest, total };
}

export type FooterState = "running" | "paused" | "stopped" | null;

/** Which footer a node, step or task gets (Decisions §5 Pause on a path):
 *  running → Pause, Skip; paused → Resume, Skip; done or stopped → Retry
 *  when the item is stopped (`PathFooter`, `status.retryable`); not reached →
 *  none. */
export function footerState(item: ItemDetail, sessions: WorkerSession[]): FooterState {
  if (!sessions.length) return null;
  const live = sessions.some((s) => LIVE.has(s.status));
  if (item.display_status === "paused" && (live || sessions.some((s) => s.status === "paused"))) return "paused";
  if (live && !["failed", "cancelled", "done", "archived"].includes(item.display_status ?? "")) return "running";
  if (["cancelled", "archived"].includes(item.display_status ?? "")) return null;
  return "stopped";
}

/** A glyph state as a word, for pane subtitles. */
export const stateWord = (s: GlyphState | undefined) =>
  ({ done: "done", current: "running", todo: "not started", failed: "failed", amber: "waiting", plain: "stopped", esc: "escalated", ghost: "removed" } as Record<string, string>)[s ?? "todo"];

/** A session's state in words: "running now", or why it waits ("needs you", "paused", "waiting"), or its end. */
export function lookWord(look: ReturnType<typeof sessionLook>): string {
  if (look.running) return "running now";
  if (look.meta === "skipped") return "skipped";
  if ((look.state === "current" || look.state === "amber") && look.meta) return look.meta;
  return stateWord(look.state);
}
