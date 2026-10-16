import { LIVE_SESSION_STATUSES } from "../../../src/types/vocab.generated";
import type { ItemBundle, Scenario } from "./fixtures";
import { pendingRewind, type Transitions } from "./transitions";

/**
 * Live motion for the dashboard demo (`MockOptions.motion`): the board's items walk their chains on their own, as
 * the executor would, through transitions.ts's doors only. Off unless asked, so the contract suite and every unit
 * row stay still.
 *
 * One tick every 8–15 s (jittered). Each tick first re-enters any board item that is `waiting` or `rate_limited`
 * once its `retry_at` has passed (`store.mark_reentered`, as its poller does), then moves every board item
 * (`S.ngBoard`) whose status is `active` one step. Every node but a gate takes two ticks:
 * - an agent node runs its first agent task: `node_started`, `worker_session_created`, `worker_session_started`;
 *   on the next tick `worker_session_exited` with its usage, then `node_completed`;
 * - a node with no agent task (builtin, forge, a command) has a session per task, created on the first tick and
 *   exited on the next (no `worker_session_started`, no usage), as the server writes them;
 * and the tick that completes a node enters the next one, or completes the item past the last. A gate is
 * requested (`node_started`, `gate_requested`) and the item stops there for a person. Loops run once, in chain
 * order. A spend cap reached stops the item before its next agent task launches. Where an item goes on from is
 * read from its own events, so whatever the visitor did meanwhile (approve, reject, skip a node or a task, retry,
 * resume, a review's request for changes) is where it carries on.
 *
 * When no board item is active and none waits at a gate, a tick files a fresh item and starts it, so a gate always
 * comes round again; a new one is filed only once everything else has stopped, finished or is waiting.
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

/** The positional events: the newest of these says whether the node the item stands on is behind it (completed,
 *  skipped, its gate approved) or still to run. Anything else that moves the item (a rejection, a retry, a resume
 *  at a rewind or of a never-started item) moves `current_node_id` or follows a `node_started`, so the node it stands
 *  on runs. A resume after a skip passes the skipped node, as the server's walk does. */
const WHERE = new Set(["node_started", "node_completed", "node_skipped", "scope_skipped", "gate_approved"]);
/** The events that open or close a gate: the newest names whether it is cleared (`gates.gate_cleared`). */
const GATE_WORD = new Set(["gate_requested", "gate_approved", "gate_rejected", "gate_reopened"]);

type Task = { path: string; agent: boolean };

export function motion(S: Scenario, T: Transitions, o: MotionOptions = {}): Motion {
  const [lo, hi] = o.every ?? [8_000, 15_000];
  const random = o.random ?? Math.random;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let paused = false;
  let stopped = false;
  let filed = 0;

  /** The paths a `scope_skipped` passed over: a task, or a step and every task in it. */
  const skipped = (b: ItemBundle) => b.events.filter((e) => e.type === "scope_skipped").map((e) => String(e.payload.path));
  const under = (path: string, scope: string) => path === scope || path.startsWith(`${scope}.`);

  /** A node's tasks in order, each kinded as the frozen chain kinds it (no frozen chain: every task an agent's),
   *  less those a scope skip passed over. */
  const tasksOf = (b: ItemBundle, node: any): Task[] => {
    let frozen: any = null;
    try { frozen = b.item.materialized_chain ? JSON.parse(b.item.materialized_chain) : null; } catch { /* none: all agents' */ }
    const own = frozen?.chain?.nodes?.find((n: any) => n.id === node.id);
    const all: Task[] = own
      ? (own.steps ?? []).flatMap((st: any) => (st.tasks ?? []).map((t: any) => ({ path: `${node.id}.${st.id}.${t.id}`, agent: t.kind === "agent" })))
      : (node.tasks ?? []).map((path: string) => ({ path, agent: true }));
    const gone = skipped(b);
    return all.filter((t) => !gone.some((g) => under(t.path, g)));
  };
  /** Whether a session's task is an agent's; one the frozen chain does not name (a steer's on a never-run node) is. */
  const isAgent = (b: ItemBundle, s: any) => {
    const node = (b.item.chain_definition?.nodes ?? []).find((n: any) => n.id === s.node_id);
    return tasksOf(b, node ?? { id: s.node_id }).find((t) => t.path === s.hook_point)?.agent ?? true;
  };

  /** `gates.gate_cleared`: the newest gate event is this gate's approval. */
  const cleared = (b: ItemBundle, gate: string) => {
    const last = [...b.events].reverse().find((e) => GATE_WORD.has(e.type));
    return last?.type === "gate_approved" && last.payload.gate === gate;
  };

  /** How the item stands on its node, by its newest positional event: `done` past it (completed, skipped, its gate
   *  approved), `skipped` when a task or step of it was skipped (the walk runs one task a node, so that ends it),
   *  else `again`: the node runs (a rejection's target, a retry, a resume, a node never started or left mid-way). */
  const standing = (b: ItemBundle): "done" | "skipped" | "again" => {
    const cur = b.item.current_node_id;
    const last = [...b.events].reverse().find((e) => WHERE.has(e.type));
    if (!last || !cur) return "again";
    const p = last.payload as Record<string, unknown>;
    if ((last.type === "node_completed" || last.type === "node_skipped") && p.node_id === cur) return "done";
    if (last.type === "gate_approved" && p.gate === cur) return "done";
    if (last.type === "scope_skipped" && under(String(p.path), cur)) return "skipped";
    return "again";
  };

  /** The node after the one the item stands on, once that one is done: a pending rewind's target at or before it
   *  (`walk.run_once`), else the next. With `again`, the one it stands on (the first, for an item never started). */
  const nextNode = (b: ItemBundle, again: boolean): any | undefined => {
    const nodes: any[] = b.item.chain_definition?.nodes ?? [];
    const at = nodes.findIndex((n) => n.id === b.item.current_node_id);
    if (at < 0) return nodes[0];
    if (again) return nodes[at];
    const rewind = pendingRewind(b);
    const back = rewind ? nodes.findIndex((n) => n.id === rewind.target) : -1;
    return back >= 0 && back <= at ? nodes[back] : nodes[at + 1];
  };
  const after = (b: ItemBundle, node: any) => {
    const nodes: any[] = b.item.chain_definition?.nodes ?? [];
    return nodes[nodes.findIndex((n) => n.id === node.id) + 1];
  };

  /** The walk entering `node`, or past the last one completing the item. */
  const enter = (id: string, node: any | undefined): void => {
    const b = S.bundles[id];
    if (!node) { T.markCompleted(id); return; }
    if (node.kind === "gate") {
      // A gate this item already cleared is passed over, with no second `node_started` (`walk.run_once`).
      if (cleared(b, node.id)) return enter(id, after(b, node));
      T.enterNode(id, node.id);
      T.requestGate(id, node.id);
      return;
    }
    T.enterNode(id, node.id);
    const tasks = tasksOf(b, node);
    const agent = tasks.find((t) => t.agent);
    if (agent) {
      // `dispatch.over_budget`, before an agent task launches: a cap reached stops the item here.
      const cap = b.item.budget_cap?.cap_usd;
      if (cap != null && (b.item.budget_cap?.spent_usd ?? 0) >= cap) { T.stopForBudget(id, node.id); return; }
      T.sessionRunning(id, T.createSession(id, node.id, agent.path).body.session_id);
      return;
    }
    // A node with nothing left to run completes as it starts.
    if (!tasks.length) { T.completeNode(id, node.id); return enter(id, after(b, node)); }
    for (const t of tasks) T.createSession(id, node.id, t.path);
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
    // A steer's next attempt waits pending: the walk enters its node again and starts it (`dispatch` enters the node
    // before its tasks), and it ends on the next tick.
    const queued = live.find((s) => s.status === "pending" && isAgent(b, s));
    if (queued) {
      T.enterNode(id, node);
      T.sessionRunning(id, queued.id);
      return;
    }
    if (live.length) {
      for (const s of live) T.sessionExited(id, s.id, isAgent(b, s) ? usage() : null);
      T.completeNode(id, node);
      return enter(id, nextNode(b, false));
    }
    const how = standing(b);
    if (how === "skipped") T.completeNode(id, node);
    enter(id, nextNode(b, how === "again"));
  };

  const tick = () => {
    timer = null;
    try {
      const board = S.ngBoard.filter((id) => S.bundles[id]);
      const now = Date.parse(T.now());
      for (const id of board) {
        const it = S.bundles[id].item;
        if ((it.status === "waiting" || it.status === "rate_limited") && it.retry_at && Date.parse(it.retry_at) <= now) T.reenter(id);
      }
      const active = board.filter((id) => S.bundles[id].item.status === "active");
      for (const id of active) step(id);
      const gated = board.some((id) => S.bundles[id].item.status === "needs_human" && S.bundles[id].item.pending_gate);
      if (!active.length && !gated) {
        const n = filed++;
        const made = T.create({ title: FRESH_TITLES[n % FRESH_TITLES.length], repo: FRESH_REPOS[n % FRESH_REPOS.length], chain_template: "default", autostart: true });
        if (made.status === 201) step(made.body.id);
      }
    } finally {
      arm();
    }
  };

  /** One timer at a time: a resume while one is pending (the demo's boot: armed here, then `whileShown` resumes a
   *  shown tab) starts no second chain. */
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
