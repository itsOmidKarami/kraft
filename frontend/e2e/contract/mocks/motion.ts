import { LIVE_SESSION_STATUSES } from "../../../src/types/vocab.generated";
import type { Scenario } from "./fixtures";
import type { Transitions } from "./transitions";

/**
 * Live motion for the dashboard demo (`MockOptions.motion`): the board's running items walk their chains on
 * their own, as the executor would, through transitions.ts's doors only. Off unless asked, so the contract
 * suite and every unit row stay still.
 *
 * One tick every 8–15 s (jittered), and each tick moves every board item (`S.ngBoard`) whose status is
 * `active` one step: an agent node starts a session (`node_started`, `worker_session_created`,
 * `worker_session_started`) and ends it on the next tick (`worker_session_exited` with its usage, then
 * `node_completed`), entering the next node at once; a node with no agent task starts and completes in one
 * tick, with no session; a gate is requested and the item stops there for a person. Loops run once, in chain
 * order. Past the last node the item completes. Where an item stands is read from its own events, so whatever
 * the visitor did meanwhile (approve, reject, skip, retry, resume) is where it goes on from.
 *
 * When nothing on the board is active and no gate waits, a tick files a fresh item and starts it, so a gate
 * always comes round again; a new one is filed only once everything else has stopped or finished.
 */

export interface MotionOptions {
  /** The pause before each tick, in ms, drawn from [min, max]. Default 8–15 s. */
  every?: [number, number];
  /** Draws the jitter and each session's usage. Default `Math.random`. */
  random?: () => number;
}

/** The simulator's handle: the demo pauses it while the tab is hidden. */
export interface Motion {
  pause(): void;
  resume(): void;
  /** For good: no tick runs again. */
  stop(): void;
}

/** What a refiled item is called, in turn. */
export const FRESH_TITLES = [
  "Cache the forge's merge request lookups",
  "Show each session's model in the log header",
  "Retry a flaky CI poll once before stopping",
  "Add a --since filter to kraft view events",
  "Warn when a gate names no artifact",
];
const FRESH_REPOS = ["kraft-core", "kraft-api", "kraft-plugins", "kraft-docs"].map((r) => `/Users/dev/code/${r}`);
const MODEL = "claude-sonnet-4-5";

/** The positional events: the newest of these says where an item goes on from. */
const WHERE = new Set(["node_started", "node_completed", "node_skipped", "gate_rejected", "work_item_retried", "work_item_resumed", "work_item_created"]);

export function motion(S: Scenario, T: Transitions, o: MotionOptions = {}): Motion {
  const [lo, hi] = o.every ?? [8_000, 15_000];
  const random = o.random ?? Math.random;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let paused = false;
  let stopped = false;
  let filed = 0;

  /** The first agent task of a node, as the frozen chain kinds it; null for a node with none (builtin, forge, a command). */
  const agentTask = (it: any, node: any): string | null => {
    let frozen: any = null;
    try { frozen = it.materialized_chain ? JSON.parse(it.materialized_chain) : null; } catch { /* no frozen chain: every task is an agent's */ }
    const own = frozen?.chain?.nodes?.find((n: any) => n.id === node.id);
    if (!own) return node.tasks?.[0] ?? null;
    for (const st of own.steps ?? []) for (const t of st.tasks ?? []) if (t.kind === "agent") return `${node.id}.${st.id}.${t.id}`;
    return null;
  };

  /** Where the item goes from the node it stands on, by its newest positional event: on past a node done or
   *  skipped, else (a rejection's target, a retry, a resume, a node left mid-way) the node it stands on again. */
  const nextNode = (id: string): any | undefined => {
    const { item: it, events } = S.bundles[id];
    const nodes: any[] = it.chain_definition?.nodes ?? [];
    const at = nodes.findIndex((n) => n.id === it.current_node_id);
    if (at < 0) return nodes[0];
    const last = [...events].reverse().find((e) => WHERE.has(e.type));
    const passed = (last?.type === "node_completed" || last?.type === "node_skipped") && last.payload.node_id === it.current_node_id;
    return passed ? nodes[at + 1] : nodes[at];
  };

  /** The walk entering `node`: an agent's session starts, a node with no agent task runs through, a gate opens. */
  const enter = (id: string, node: any | undefined) => {
    if (!node) return T.markCompleted(id);
    T.enterNode(id, node.id);
    if (node.kind === "gate") return T.requestGate(id, node.id);
    const task = agentTask(S.bundles[id].item, node);
    if (!task) return T.completeNode(id, node.id);
    const sid = T.createSession(id, node.id, task).body.session_id;
    return T.sessionRunning(id, sid);
  };

  const usage = () => ({
    model: MODEL,
    tokens_in: 28_000 + Math.round(random() * 40_000),
    tokens_out: 3_000 + Math.round(random() * 6_000),
    cost_usd: Math.round((0.04 + random() * 0.16) * 100) / 100,
  });

  /** One step of one active item. */
  const step = (id: string) => {
    const b = S.bundles[id];
    const node = b.item.current_node_id;
    const live = b.sessions.filter((s) => s.node_id === node && s.hook_point !== "escalation" && LIVE_SESSION_STATUSES.includes(s.status));
    // A steer's next attempt waits pending: it starts now, and ends on the next tick.
    const pending = live.find((s) => s.status === "pending");
    if (pending) return T.sessionRunning(id, pending.id);
    if (live.length) {
      for (const s of live) T.sessionExited(id, s.id, usage());
      T.completeNode(id, node);
    }
    return enter(id, nextNode(id));
  };

  const tick = () => {
    timer = null;
    const board = S.ngBoard.filter((id) => S.bundles[id]);
    const active = board.filter((id) => S.bundles[id].item.status === "active");
    for (const id of active) step(id);
    const gated = board.some((id) => S.bundles[id].item.status === "needs_human" && S.bundles[id].item.pending_gate);
    if (!active.length && !gated) {
      const n = filed++;
      const made = T.create({ title: FRESH_TITLES[n % FRESH_TITLES.length], repo: FRESH_REPOS[n % FRESH_REPOS.length], chain_template: "default", autostart: true });
      if (made.status === 201) step(made.body.id);
    }
    arm();
  };

  const arm = () => {
    if (paused || stopped || timer) return;
    timer = setTimeout(tick, lo + random() * (hi - lo));
  };
  const disarm = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };

  arm();
  return {
    pause() { paused = true; disarm(); },
    resume() { paused = false; arm(); },
    stop() { stopped = true; disarm(); },
  };
}
