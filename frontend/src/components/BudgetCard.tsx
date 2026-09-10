import { useState } from "react";
import { Coins } from "@phosphor-icons/react";
import * as api from "../api";
import type { WorkerSession, WorkItem } from "../types";
import { Escalate } from "./Escalate";

const usd = (n: number) => `$${n.toFixed(2)}`;

/**
 * The spend-cap attention card (sub-project E §3).
 *
 * The cap refused to START the next agent task. It did not interrupt one that
 * was running and could not have: the agent CLI only reports its cost when the
 * session exits. The card says so, because a card that reads like a hard
 * ceiling is how someone ends up with a bill and a reasonable complaint.
 *
 * Retry is always offered. It used to be gated on the node having a fix loop,
 * because `/retry` 409ed without one — Kraft-bzwi removed that 409, and a budget
 * stop on a loopless node has no other door (resume wants a paused item, pause
 * wants a running one, approve/reject want a gate). Retrying clears nothing, so
 * the hint says the run stops again unless the cap is raised or cleared first.
 */
export function BudgetCard({
  item,
  sessions,
}: {
  item: WorkItem;
  sessions?: WorkerSession[];
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const b = item.budget;
  if (!b) return null;

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
            Raise or clear the cap in Settings → Policy, then retry.
          </span>
        </div>
      </div>
      <div className="gate-actions capped-actions">
        <button className="btn btn-secondary" disabled={busy} onClick={retry}>
          Retry anyway
        </button>
        <Escalate item={item} sessions={sessions} />
        <span className="control-hint">
          retry clears nothing — the spend stands, so this stops again at the next
          agent task unless the cap is raised or cleared first
        </span>
      </div>
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}
