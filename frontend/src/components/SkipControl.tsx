import { useState } from "react";
import { SkipForward } from "@phosphor-icons/react";
import * as api from "../api";

/**
 * The Skip control, offered everywhere a chain step can be bypassed: the
 * running control row, a stopped plain node (CappedCard), a pending gate
 * (Gate), and a paused item (PausedCard). One component so the toggle →
 * optional note → confirm/cancel shape (Gate.tsx's rejectForm pattern)
 * stays in step across all four instead of drifting apart.
 */
export function SkipControl({
  itemId,
  disabled,
}: {
  itemId: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const skip = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.skipWorkItem(itemId, note.trim() || undefined);
      setOpen(false);
      setNote("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button className="btn btn-ghost" disabled={disabled} onClick={() => setOpen(true)}>
        <SkipForward size={14} />
        Skip…
      </button>
    );
  }

  return (
    <div className="gate-reject">
      <textarea
        className="input"
        aria-label="skip note"
        placeholder="Why skip this? (optional)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <div className="gate-actions">
        <button className="btn btn-primary" disabled={busy} onClick={skip}>
          Skip step
        </button>
        <button className="btn btn-ghost" disabled={busy} onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}
