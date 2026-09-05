import { useState } from "react";
import { Play } from "@phosphor-icons/react";
import * as api from "../api";
import type { WorkItem, WorkerSession } from "../types";

/**
 * The paused control card (design 4c). Pause and steer are one mechanism: the
 * attempt was killed, and resuming launches a fresh one — with the note leading
 * its prompt, or without.
 */
export function PausedCard({
  item,
  sessions,
}: {
  item: WorkItem;
  sessions: WorkerSession[];
}) {
  const [steer, setSteer] = useState(item.pending_steer_context ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const paused = sessions.filter(
    (s) => s.node_id === item.current_node_id && s.status === "paused",
  );
  const attempt = paused.length ? paused[0].attempt + 1 : 2;
  // An agent-created item (design §6 rule 1) is paused without ever having run:
  // no sessions, no current node. "Resume attempt 2" would be false in every word.
  const neverStarted = item.current_node_id === null;

  const resume = async (withSteer: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      await api.resumeWorkItem(item.id, withSteer ? steer.trim() || undefined : undefined);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (neverStarted) {
    // No steer box: steer is context carried into the next attempt of something
    // already tried, and there is no previous attempt to steer away from.
    return (
      <div className="card elev-sm paused-card" data-testid="paused-card">
        <p className="field-hint">
          Waiting to start · created by an agent, so nothing runs until you say so
        </p>
        <div className="gate-actions capped-actions">
          <button className="btn btn-primary" disabled={busy} onClick={() => resume(false)}>
            <Play size={14} />
            Start
          </button>
        </div>
        {err && <p className="form-error">{err}</p>}
      </div>
    );
  }

  return (
    <div className="card elev-sm paused-card" data-testid="paused-card">
      <div className="field">
        <label htmlFor="paused-steer">
          Steer{" "}
          <span className="field-hint">
            · goes into the next attempt's system prompt, never into the repo
          </span>
        </label>
        <textarea
          id="paused-steer"
          className="input"
          value={steer}
          onChange={(e) => setSteer(e.target.value)}
        />
      </div>
      <div className="gate-actions capped-actions">
        <button
          className="btn btn-primary"
          disabled={busy || steer.trim() === ""}
          onClick={() => resume(true)}
        >
          <Play size={14} />
          Resume with steer
        </button>
        <button className="btn btn-ghost" disabled={busy} onClick={() => resume(false)}>
          Resume without
        </button>
        <span className="control-hint">
          relaunches {paused[0]?.hook_point ?? item.current_node_id} as attempt {attempt}
        </span>
      </div>
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}
