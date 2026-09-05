import { useState } from "react";
import { Coins } from "@phosphor-icons/react";
import * as api from "../api";
import type { WorkItem } from "../types";

const usd = (n: number) => `$${n.toFixed(2)}`;

/**
 * The spend-cap attention card (sub-project E §3).
 *
 * The cap refused to START the next agent task. It did not interrupt one that
 * was running and could not have: the agent CLI only reports its cost when the
 * session exits. The card says so, because a card that reads like a hard
 * ceiling is how someone ends up with a bill and a reasonable complaint.
 *
 * Retrying does not clear anything — the money is spent and the sum will not go
 * down — so the retry control only appears where `POST /retry` would actually
 * be accepted (a node with a fix loop), and it says the run will stop again
 * unless the cap is raised or cleared first.
 *
 * Where there is no fix loop there is no control at all — `/retry` 409s and
 * `/resume` only takes a paused item — so the copy says raising the cap will not
 * restart this one, rather than sending the operator to Settings for nothing.
 */
export function BudgetCard({ item }: { item: WorkItem }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const b = item.budget;
  if (!b) return null;
  const node = item.chain_definition.nodes.find((n) => n.id === item.current_node_id);
  const canRetry = !!node?.fix_loop;

  const retry = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.retryWorkItem(item.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card attention-card" data-testid="budget-card">
      <div className="attention-head">
        <Coins size={18} className="attention-glyph" />
        <div className="attention-text">
          <span className="attention-title">
            {b.scope === "daily"
              ? `today's budget is spent — ${usd(b.spent_usd)} of ${usd(b.cap_usd)}`
              : `this work item's budget is spent — ${usd(b.spent_usd)} of ${usd(b.cap_usd)}`}
          </span>
          <span className="attention-sub">
            {b.scope === "daily"
              ? "This stops every work item on this instance until local midnight or a higher cap. "
              : "No new agent task was started for this item. "}
            A running agent was not interrupted — cost is only known once a session
            ends, so the overshoot is one task, not the cap.{" "}
            {canRetry
              ? "Raise or clear the cap in Settings → Policy, then retry."
              : "Raising the cap in Settings → Policy will not restart this item — this node has no retry. It applies to the next item you start."}
          </span>
        </div>
      </div>
      {canRetry && (
        <div className="gate-actions capped-actions">
          <button className="btn btn-secondary" disabled={busy} onClick={retry}>
            Retry anyway
          </button>
          <span className="control-hint">
            retry clears nothing — the spend stands, so this stops again at the next
            agent task unless the cap is raised or cleared first
          </span>
        </div>
      )}
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}
