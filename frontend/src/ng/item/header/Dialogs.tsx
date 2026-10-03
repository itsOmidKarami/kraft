import { useState, type RefObject } from "react";
import { Button } from "../../ui/Button";
import { Field } from "../../ui/Field";
import { Popover } from "../../ui/Popover";
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

type CardProps = { id: string; anchor: RefObject<HTMLElement | null>; onClose: () => void; onDone: () => void };

/** Escalate… (prototype lines 37–41; GAP §2 #14 "in a new thread"): a card
 *  under the main button, as Pause and Cancel are. */
export function EscalateCard({ id, anchor, onClose, onDone }: CardProps) {
  const [message, setMessage] = useState("");
  const [fresh, setFresh] = useState(false);
  const { busy, error, go } = useSubmit(() => act.escalate(id, message.trim(), fresh), onDone);
  return (
    <Popover anchor={anchor} open notch onClose={onClose} role="dialog" label="Escalate this item" dirty={!!message.trim()}>
      <div className="item-card-pop">
        <h2 className="item-pop-title">Escalate this item</h2>
        <p className="item-muted">The escalation agent reads the whole item: every node, round, finding and earlier turn. It either decides and resumes, or comes back to you with a question. The run keeps going meanwhile.</p>
        <Field label="Message" error={error}>
          <textarea className="item-input" rows={3} value={message} onChange={(e) => setMessage(e.target.value)} onKeyDown={sendOnModEnter(go, !busy && !!message.trim())} />
        </Field>
        <label className="item-check"><input type="checkbox" checked={fresh} onChange={(e) => setFresh(e.target.checked)} /> Start a new thread</label>
        <div className="item-actions">
          <Button variant="primary" disabled={busy || !message.trim()} onClick={go}>Escalate</Button>
          <Button onClick={onClose}>Cancel</Button>
        </div>
      </div>
    </Popover>
  );
}

/** Mark complete… (prototype line 49), a card under the main button. The server requires a reason. */
export function CompleteCard({ id, anchor, onClose, onDone }: CardProps) {
  const [reason, setReason] = useState("");
  const [beads, setBeads] = useState(false);
  const { busy, error, go } = useSubmit(() => act.complete(id, reason.trim(), beads), onDone);
  return (
    <Popover anchor={anchor} open notch onClose={onClose} role="dialog" label="Mark this item complete?" dirty={!!reason.trim()}>
      <div className="item-card-pop">
        <h2 className="item-pop-title">Mark this item complete?</h2>
        <p className="item-muted">For work that landed somewhere else, or no longer needs the chain. It stops whatever is running and skips the remaining nodes.</p>
        <Field label="Reason" hint="Goes in the run log." error={error}>
          <textarea className="item-input" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} onKeyDown={sendOnModEnter(go, !busy && !!reason.trim())} />
        </Field>
        <label className="item-check"><input type="checkbox" checked={beads} onChange={(e) => setBeads(e.target.checked)} /> Also close its beads</label>
        <div className="item-actions">
          <Button variant="primary" disabled={busy || !reason.trim()} onClick={go}>Mark complete</Button>
          <Button onClick={onClose}>Keep it going</Button>
        </div>
      </div>
    </Popover>
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
