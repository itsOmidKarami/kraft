import { useState } from "react";
import { ChatText, Prohibit } from "@phosphor-icons/react";
import * as api from "../api";
import { elapsed } from "../format";
import type { KraftEvent, WorkItem, WorkerSession } from "../types";
import { Escalate, escalating } from "./Escalate";
import { LogModal } from "./LogModal";

/**
 * The stranded-fix-loop attention card (design 4b). It replaces the control row
 * when a fix loop has stopped needing a human — either it burned its cap
 * (`item.cappedOut` set) or the loop-eligible findings stopped changing between
 * cycles (`no_progress`, `cappedOut` absent): what the loop was, how long it
 * ran, what each cycle left failing, and the one way forward — a steer note
 * that leads cycle 1 of the retry.
 */

interface Cycle {
  n: number;
  failed: string[];
  sessionId: string | null;
}

/**
 * One row per fix cycle, from the `fix_cycle_started` events.
 *
 * `fix_cycle_started {cycle: n}` reports what the measurement stamped
 * `round = n - 1` found: the loop measures, counts the failure as cycle n, then
 * dispatches the fix at round n. So the log link for "cycle n · X failing" is the
 * session that did the measuring, one round back — not the fix that followed it.
 */
function cycles(events: KraftEvent[], sessions: WorkerSession[], nodeId: string | null): Cycle[] {
  return events
    .filter((e) => e.type === "fix_cycle_started" && e.payload.node_id === nodeId)
    .map((e) => {
      const n = e.payload.cycle as number;
      const failed = (e.payload.failed_tasks as string[]) ?? [];
      const measured = sessions.find(
        (s) => s.node_id === nodeId && s.round === n - 1 && failed.includes(s.hook_point),
      );
      return { n, failed, sessionId: measured?.id ?? null };
    });
}

/** How long the loop ran: first fix cycle to the moment it was stopped. */
function loopSpan(events: KraftEvent[], nodeId: string | null): string | null {
  const first = events.find(
    (e) => e.type === "fix_cycle_started" && e.payload.node_id === nodeId,
  );
  const stopped = [...events].reverse().find((e) => e.type === "work_item_needs_human");
  if (!first || !stopped) return null;
  const ms = Date.parse(stopped.created_at) - Date.parse(first.created_at);
  return Number.isNaN(ms) || ms < 0 ? null : elapsed(ms);
}

export function CappedCard({
  item,
  sessions,
  events,
}: {
  item: WorkItem;
  sessions: WorkerSession[];
  events: KraftEvent[];
}) {
  const [steer, setSteer] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [logSid, setLogSid] = useState<string | null>(null);

  const node = item.chain_definition.nodes.find((n) => n.id === item.current_node_id);
  const rows = cycles(events, sessions, item.current_node_id);
  const span = loopSpan(events, item.current_node_id);
  const failedHooks = [...new Set(rows.flatMap((c) => c.failed))];
  // The server 409s an explicit steer on a node with no agent task to carry it
  // to (Kraft-bz9b) -- offering the box there would just make retry fail.
  // Undefined (an older cached response) fails open, same as the server does.
  const steerable = item.steerable !== false;
  // `_guard`'s crash handler stops the item wherever it stood, which may be a
  // fix-loop node — the same shape as a `no_progress` escalation. The reason
  // string is what tells them apart (Kraft-esc); retry is still the way out.
  const crashed = item.stop_reason?.startsWith("executor crashed:") ?? false;
  // An escalation turn is already in this worktree; a concurrent retry would
  // race it (both touch the same checkout), and "no agent to steer" reads as
  // a lie while `Escalate` shows one running right there.
  const escalatingNow = escalating(sessions);

  const retry = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.retryWorkItem(item.id, steerable ? steer.trim() || undefined : undefined);
      setSteer("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card attention-card" data-testid="capped-card">
      <div className="attention-head">
        <Prohibit size={18} className="attention-glyph" />
        <div className="attention-text">
          <span className="attention-title">
            {crashed ? (
              "Kraft crashed while running this node"
            ) : (
              <>
                {node?.fix_loop ?? node?.id ?? "this node"}{" "}
                {item.cappedOut ? "hit its cap" : steerable ? "needs a steer" : "failed"}
                {item.cappedOut && ` — ${item.cappedOut.attempts} attempts`}
                {span && `, ${span}`}
              </>
            )}
          </span>
          <span className="attention-sub">
            {crashed
              ? `${item.stop_reason} `
              : failedHooks.length > 0 && `${failedHooks.join(", ")} never went clean. `}
            The chain did not move; nothing was merged.
          </span>
        </div>
      </div>

      {rows.length > 0 && (
        <div className="cycle-trace">
          {rows.map((c) => (
            <div key={c.n} className="cycle-row" data-cycle={c.n}>
              <span className="cycle-n">cycle {c.n}</span>
              <span className="cycle-failed">
                {c.failed.length} failing · {c.failed.join(", ")}
              </span>
              {c.sessionId ? (
                <button className="btn btn-ghost cycle-log" onClick={() => setLogSid(c.sessionId)}>
                  log
                </button>
              ) : (
                <span />
              )}
            </div>
          ))}
        </div>
      )}

      {steerable && (
        <div className="field">
          <label htmlFor="capped-steer">
            Steer <span className="field-hint">· goes into cycle 1 of the retry, never into the repo</span>
          </label>
          <textarea
            id="capped-steer"
            className="input"
            value={steer}
            onChange={(e) => setSteer(e.target.value)}
            placeholder="What did the agent keep getting wrong?"
          />
        </div>
      )}

      <div className="gate-actions capped-actions">
        <button className="btn btn-primary" disabled={busy || escalatingNow} onClick={retry}>
          <ChatText size={14} />
          {steerable ? "Steer and retry" : "Retry"}
        </button>
        <Escalate item={item} sessions={sessions} />
      </div>
      {!escalatingNow && (
        <p className="control-hint capped-hint">
          {steerable
            ? "retry resets the loop counter; steer text carries into cycle 1"
            : "this node has no agent to steer — retry just re-runs it"}
        </p>
      )}
      {err && <p className="form-error">{err}</p>}
      {logSid && <LogModal sessionId={logSid} onClose={() => setLogSid(null)} />}
    </div>
  );
}
