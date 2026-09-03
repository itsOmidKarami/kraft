import { useState } from "react";
import * as api from "../api";
import type { WorkItem } from "../types";

export function Gate({ item, gate }: { item: WorkItem; gate: string }) {
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="gate" data-gate={gate}>
      <span className="gate-label">⚑ {gate}</span>
      {!rejecting && (
        <>
          <button disabled={busy} onClick={() => act(() => api.approveGate(item.id, gate))}>
            Approve
          </button>
          <button disabled={busy} onClick={() => setRejecting(true)}>Reject…</button>
        </>
      )}
      {rejecting && (
        <div className="gate-reject">
          <textarea aria-label="reject note" value={note} onChange={(e) => setNote(e.target.value)} />
          <button
            disabled={busy || note.trim() === ""}
            onClick={() => act(() => api.rejectGate(item.id, gate, note))}
          >
            Submit rejection
          </button>
        </div>
      )}
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}
