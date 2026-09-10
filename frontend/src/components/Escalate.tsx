import { useState } from "react";
import { Robot } from "@phosphor-icons/react";
import * as api from "../api";
import type { WorkerSession, WorkItem } from "../types";
import { LogModal } from "./LogModal";

/**
 * The manual door onto a `needs_human` stop, alongside whatever the stop's own
 * card already offers (approve/reject, retry, answer). One shared control
 * rather than one per card: every needs_human variant gets the same
 * collapse -> message box -> send shape, the same one `Gate`'s reject flow
 * already uses.
 *
 * `sessions` is optional: the board's inline row has no per-item session list
 * to check, so it skips the running-state detection below and just sends —
 * a second click while one is already in flight surfaces the server's own
 * 409 as the same inline error every other action here shows.
 */
/** An escalation session still in flight -- the one predicate both this
 * component's own `running` and the exported `escalating()` build on, so the
 * two can never disagree about what "in flight" means. */
function inFlight(s: WorkerSession): boolean {
  return s.hook_point === "escalation" && (s.status === "pending" || s.status === "running");
}

/** Whether an escalation turn is in flight for this item -- exported so a
 * stop card (e.g. `CappedCard`) can suppress its own retry/steer copy
 * instead of contradicting this component's "agent is on it" state. */
export function escalating(sessions: WorkerSession[]): boolean {
  return sessions.some(inFlight);
}

export function Escalate({ item, sessions = [] }: { item: WorkItem; sessions?: WorkerSession[] }) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);

  // Not checked while `open`: a session poll landing a matching escalation
  // turn mid-draft (e.g. sent from another tab) would otherwise blow away
  // whatever is being typed here with no warning. Left open, a stale send
  // surfaces the server's own 409 inline instead -- same as the board's
  // inline row, which has no sessions to check at all.
  const running = open ? undefined : sessions.find(inFlight);

  if (running) {
    return (
      <div className="gate-actions">
        <span className="control-hint">Kraft agent is on it!</span>
        <button className="btn btn-ghost" onClick={() => setShowLog(true)}>
          View log
        </button>
        {showLog && <LogModal sessionId={running.id} onClose={() => setShowLog(false)} />}
      </div>
    );
  }

  const send = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.escalateWorkItem(item.id, message.trim());
      setMessage("");
      setOpen(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button className="btn btn-ghost" onClick={() => setOpen(true)}>
        <Robot size={14} />
        Escalate…
      </button>
    );
  }

  return (
    <div className="gate-escalate">
      <textarea
        className="input"
        aria-label="escalate message"
        placeholder="What should the agent look at?"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
      />
      <div className="gate-actions">
        <button
          className="btn btn-secondary"
          disabled={busy || message.trim() === ""}
          onClick={send}
        >
          Send to agent
        </button>
        <button className="btn btn-ghost" disabled={busy} onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}
