import { useState } from "react";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { Field } from "../../ui/Field";
import { act, type Done } from "../actions";
import { useFocusSoon } from "../useFocusSoon";
import { sendOnModEnter } from "../../keys";

function useSubmit(send: () => Promise<Done>, onDone: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setBusy(true);
    setError(null);
    const r = await send();
    setBusy(false);
    if (r.ok) onDone();
    else setError(r.error);
  };
  return { busy, error, go };
}

/** Escalate… (prototype lines 37–41; GAP §2 #14 "in a new thread"). */
export function EscalateDialog({ id, onClose, onDone }: { id: string; onClose: () => void; onDone: () => void }) {
  const [message, setMessage] = useState("");
  const [fresh, setFresh] = useState(false);
  const { busy, error, go } = useSubmit(() => act.escalate(id, message.trim(), fresh), onDone);
  return (
    <Dialog title="Escalate this item" onClose={onClose} dirty={!!message.trim()} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={busy || !message.trim()} onClick={go}>Escalate</Button></>}>
      <p className="item-muted">The escalation agent reads the whole item: every node, round, finding and earlier turn. It either decides and resumes, or comes back to you with a question. The run keeps going meanwhile.</p>
      <Field label="Message" error={error}>
        <textarea className="item-input" rows={3} value={message} onChange={(e) => setMessage(e.target.value)} onKeyDown={sendOnModEnter(go, !busy && !!message.trim())} />
      </Field>
      <label className="item-check"><input type="checkbox" checked={fresh} onChange={(e) => setFresh(e.target.checked)} /> Start a new thread</label>
    </Dialog>
  );
}

/** Mark complete… (prototype line 49). The server requires a reason. */
export function CompleteDialog({ id, onClose, onDone }: { id: string; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [beads, setBeads] = useState(false);
  const { busy, error, go } = useSubmit(() => act.complete(id, reason.trim(), beads), onDone);
  return (
    <Dialog title="Mark this item complete?" onClose={onClose} dirty={!!reason.trim()} footer={<><Button onClick={onClose}>Keep it going</Button><Button variant="primary" disabled={busy || !reason.trim()} onClick={go}>Mark complete</Button></>}>
      <p className="item-muted">For work that landed somewhere else, or no longer needs the chain. It stops whatever is running and skips the remaining nodes.</p>
      <Field label="Reason" hint="Goes in the run log." error={error}>
        <textarea className="item-input" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} onKeyDown={sendOnModEnter(go, !busy && !!reason.trim())} />
      </Field>
      <label className="item-check"><input type="checkbox" checked={beads} onChange={(e) => setBeads(e.target.checked)} /> Also close its beads</label>
    </Dialog>
  );
}

/** Pause asks first (prototype line 32). */
export function PauseConfirm({ busy, error, onPause, onClose }: { busy: boolean; error: string | null; onPause: () => void; onClose: () => void }) {
  const first = useFocusSoon<HTMLButtonElement>();
  return (
    <div className="item-card-pop">
      <h2 className="item-pop-title">Pause this item?</h2>
      <p className="item-muted">The running task stops where it is. Resume starts it again, with a steer if you leave one.</p>
      {error && <p className="item-error" role="alert">{error}</p>}
      <div className="item-actions">
        <button ref={first} type="button" className="btn btn-primary" disabled={busy} onClick={onPause}>Pause now</button>
        <Button onClick={onClose}>Cancel</Button>
      </div>
    </div>
  );
}
