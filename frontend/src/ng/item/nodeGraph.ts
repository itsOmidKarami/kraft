import { elapsed, elapsedBetween } from "../../format";
import type { ChainNode as ApiNode, WorkerSession } from "../../types";
import type { NodeStep } from "../graph/nodeLayout";
import type { GlyphState, GraphItem } from "../graph/types";
import { stepsOf, taskName } from "./paths";
import type { ItemDetail } from "./useItem";

export const ESCALATION = "escalation";
export const isEscalation = (s: WorkerSession) => s.hook_point === ESCALATION || s.hook_point.endsWith(`.${ESCALATION}`);
const LIVE = new Set(["running", "pending", "rate_limited", "waiting", "needs_context"]);

/** A task path's sessions, attempt order (the attempt switcher's list). */
export function sessionsOf(item: ItemDetail, path: string): WorkerSession[] {
  return item.worker_sessions.filter((s) => s.hook_point === path).sort((a, b) => a.attempt - b.attempt || a.created_at.localeCompare(b.created_at));
}
/** The node's escalation turns, oldest first. */
export const escalationsOf = (item: ItemDetail, node: string) => item.worker_sessions.filter((s) => s.node_id === node && isEscalation(s)).sort((a, b) => a.created_at.localeCompare(b.created_at));

/** What a session draws as: its glyph state and the one meta word (Decisions §5 Task meta: duration only). */
export function sessionLook(s: WorkerSession | undefined, now: number): Pick<GraphItem, "state" | "meta" | "running" | "paused" | "attemptStopped"> {
  if (!s) return { state: "todo" };
  if (s.status === "pending") return { state: "todo", meta: "pending" };
  if (s.status === "running") return { state: "current", running: true, meta: `running · ${elapsedBetween(s.started_at, null, now)}` };
  if (s.status === "paused") return { state: "current", paused: true, meta: "paused" };
  if (s.status === "rate_limited" || s.status === "waiting") return { state: "amber", meta: "waiting" };
  if (s.status === "needs_context") return { state: "current", meta: "needs you" };
  if (s.status === "failed" || s.status === "config_error" || s.status === "unknown") return { state: "failed" };
  if (s.status === "capped_out") return { state: "failed", attemptStopped: true };
  return { state: "done", meta: s.wall_ms != null ? elapsed(s.wall_ms) : undefined };
}

/** A node's inside for NodeGraph (Decisions §7): steps in order, a row per
 *  task with its latest attempt's state, the escalation side branch, the
 *  fix-loop arc once a round ran, and the on_failure footer line. */
export function nodeGraph(item: ItemDetail, node: ApiNode, now = Date.now()) {
  const { steps } = stepsOf(node);
  const out: NodeStep[] = steps.map((st) => ({
    id: st.id,
    tasks: st.tasks.map((path): GraphItem => {
      const ss = sessionsOf(item, path);
      const last = ss.at(-1);
      return { id: taskName(path), taskKind: ss.some((s) => s.model) ? "agent" : undefined, attempt: last?.attempt, ...sessionLook(last, now) };
    }),
  }));
  const esc = escalationsOf(item, node.id);
  const lastEsc = esc.at(-1);
  const side: GraphItem | undefined = lastEsc && { id: ESCALATION, icon: "siren", ...sessionLook(lastEsc, now), meta: `thread ${lastEsc.thread} · turn ${esc.filter((s) => s.thread === lastEsc.thread).length}` };
  const rounds = Math.max(0, ...item.worker_sessions.filter((s) => s.node_id === node.id && !isEscalation(s)).map((s) => s.round));
  const stopped = item.stop?.node === node.id && (item.stop.kind === "cap" || item.stop.kind === "budget");
  const loop = node.fix_loop && rounds > 0 ? { tone: stopped ? ("red" as const) : item.current_node_id === node.id ? ("active" as const) : ("idle" as const), label: `fix loop · round ${rounds + 1}` } : undefined;
  const onFailure = node.on_failure?.length ? node.on_failure.map(taskName).join(", ") : undefined;
  return { steps: out, side, loop, onFailure };
}

export type FooterState = "running" | "paused" | "stopped" | null;

/** Which footer a node, step or task gets (Decisions §5 Pause on a path):
 *  running → Pause, Skip; paused → Resume, Skip, Retry; done or stopped →
 *  Retry; not reached → none. */
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
