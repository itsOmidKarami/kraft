import { Robot } from "@phosphor-icons/react";
import { Row, RowState, RowText, StatusGlyph } from "../../../components/ui";
import { clock, elapsed, shortId, tokens, usd } from "../../../format";
import type { KraftEvent, WorkerSession, WorkItem } from "../../../types";

/**
 * Inspector · Tasks (UI v2 · 05, 12): one row per session, newest first.
 * Selecting a row is what the right pane's Log follows (`RightPane/Log.tsx`).
 * Reuses `CurrentNodePanel`'s metrics line; the node-grouped `<details>`
 * layout that component used is replaced by a flat, scope-toggled list —
 * the pane on the right is where a node's own detail lives now.
 */

export type Scope = "node" | "all";

/** The this node · all switch (38): Tasks' own, and Timeline's (W11 · F), one
 *  state for both, held by the Inspector. */
export function ScopeChips({
  scope,
  onScope,
  nodeId,
}: {
  scope: Scope;
  onScope: (s: Scope) => void;
  nodeId: string | null;
}) {
  return (
    <span className="inspector-scope">
      <button className="chip" aria-pressed={scope === "node"} onClick={() => onScope("node")} disabled={!nodeId}>
        this node
      </button>
      <button className="chip" aria-pressed={scope === "all"} onClick={() => onScope("all")}>
        all
      </button>
    </span>
  );
}

function metricsOf(s: WorkerSession): string {
  const parts: string[] = [];
  if (s.tokens_in != null || s.tokens_out != null) {
    parts.push(`${tokens((s.tokens_in ?? 0) + (s.tokens_out ?? 0))} tokens`);
  }
  if (s.cost_usd != null) parts.push(usd(s.cost_usd));
  if (s.wall_ms != null) parts.push(elapsed(s.wall_ms));
  return parts.join(" · ");
}

export function Tasks({
  item,
  sessions,
  events,
  nodeId,
  selected,
  onSelect,
  scope,
  onScope,
}: {
  item: WorkItem;
  sessions: WorkerSession[];
  events: KraftEvent[];
  /** The stage graph's selected node — the default scope. */
  nodeId: string | null;
  selected: string | null;
  onSelect: (sessionId: string) => void;
  scope: Scope;
  onScope: (s: Scope) => void;
}) {
  const ordered = [...sessions].sort((a, b) =>
    b.created_at.localeCompare(a.created_at),
  );
  const shown =
    scope === "node" && nodeId
      ? ordered.filter((s) => s.node_id === nodeId)
      : ordered;

  // Kraft-04fmo: what this node RUNS, whether or not it has run yet. The tab
  // renders from `worker_sessions` alone, so an unstarted node used to show
  // "no tasks on this node" -- false: there are tasks, none have started.
  // `effective_chain` first, `chain_definition` as the fallback: the former has
  // this item's own `node_overrides` folded in and is what actually runs.
  const chainNode = nodeId
    ? (item.effective_chain ?? item.chain_definition).nodes.find((n) => n.id === nodeId)
    : undefined;
  // Latest by created_at, not first: a task with several sessions has been
  // retried, re-entered after a gate rejection, or repaired, and what a reader
  // wants is where it stands now. `ordered` is already newest-first, so the
  // first match is the latest.
  const latestFor = (hook: string) =>
    ordered.find((s) => s.node_id === nodeId && s.hook_point === hook);

  return (
    <div className="inspector-list" data-testid="inspector-tasks">
      {/* Kraft-qqz8: the implementer's own plan, task by task — only while
          it's the selected node and the plan parsed at least one heading. */}
      {item.progress?.tasks && item.current_node_id === nodeId && (
        <>
          <p className="section-label">PLAN · {item.progress.tasks.length} TASKS</p>
          <ul className="plan-list" data-testid="plan-list">
            {item.progress.tasks.map((t) => (
              <li key={t.n} data-state={t.state}>
                <span className="plan-task-n">{t.n}</span>
                <span className="plan-task-title">{t.title}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {chainNode && (
        <>
          <p className="section-label">
            NODE TASKS · {chainNode.tasks.length} · CONCURRENT
          </p>
          <ul className="plan-list" data-testid="node-task-list">
            {chainNode.tasks.map((hook) => {
              const s = latestFor(hook);
              return (
                <li key={hook} data-testid={`node-task-${hook}`}>
                  {s ? (
                    <StatusGlyph status={s.status} size={13} />
                  ) : (
                    <span className="plan-task-n" aria-hidden />
                  )}
                  <span className="plan-task-title">{hook}</span>
                  <span className="row-sub">{s ? s.status : "not started"}</span>
                </li>
              );
            })}
          </ul>
        </>
      )}
      <p className="section-label">
        SESSIONS · {shown.length}
        <ScopeChips scope={scope} onScope={onScope} nodeId={nodeId} />
      </p>
      {shown.length === 0 && (
        <p className="empty">
          no tasks {scope === "node" ? "on this node" : "yet"}
        </p>
      )}
      {shown.map((s) => {
        // An escalation turn (18, 18b): its own session row, robot glyph --
        // "turn N · ses_… · 2m 08s" while running, "sent 11:41 · "<message>""
        // once it has one.
        if (s.hook_point === "escalation") {
          const sent = events.find(
            (e) =>
              e.type === "escalation_message" && e.payload.session_id === s.id,
          );
          const m = metricsOf(s);
          const sub =
            s.status === "running" || s.status === "pending"
              ? `turn ${s.attempt} · ${shortId(s.id)} · ${m || "starting…"}`
              : sent
                ? `sent ${clock(sent.created_at)} · "${sent.payload.message}"`
                : `turn ${s.attempt}`;
          return (
            <Row
              key={s.id}
              className="task-row"
              data-testid={`task-row-${s.id}`}
              data-selected={s.id === selected}
              onClick={() => onSelect(s.id)}
              columns="16px 1fr auto"
            >
              <Robot size={16} className="attention-glyph" />
              <RowText title={`escalation · turn ${s.attempt}`} sub={sub} />
              <RowState status={s.status} />
            </Row>
          );
        }
        const attempt =
          s.hook_point === "on.implementation.start" && item.fixCycle != null
            ? `cycle ${item.fixCycle}`
            : `attempt ${s.attempt}`;
        const m = metricsOf(s);
        return (
          <Row
            key={s.id}
            className="task-row"
            data-testid={`task-row-${s.id}`}
            data-selected={s.id === selected}
            onClick={() => onSelect(s.id)}
            columns="16px 1fr auto"
          >
            <StatusGlyph status={s.status} size={16} />
            <RowText
              title={s.hook_point}
              sub={`${s.node_id} · ${attempt}${m ? ` · ${m}` : ""}`}
            />
            <RowState status={s.status} />
          </Row>
        );
      })}
    </div>
  );
}
