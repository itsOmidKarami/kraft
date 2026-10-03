import { useState } from "react";
import { Button } from "../../ui/Button";
import { act } from "../actions";
import type { FooterState } from "../nodeGraph";
import type { ItemDetail } from "../useItem";
import { sendOnModEnter } from "../../keys";
import { retryable } from "../status";

const ENDED = new Set(["done", "cancelled", "archived"]);

/** A node's, step's or task's footer (Decisions §5 Pause on a path, §6 Skip):
 *  Pause, Skip and Retry in that order, by state. Skip and Retry confirm in
 *  the pane; Retry takes a steer when the item has an agent to read it.
 *  Retry is offered only on an item the server would retry (`retryable`): a
 *  paused one has Resume, and a running or waiting one nothing (R10b-01). */
export function PathFooter({ item, path, what, state: shown, reload, extra }: { item: ItemDetail; path: string; what: "node" | "step" | "task"; state: FooterState; reload: () => void; extra?: React.ReactNode }) {
  // An item that has ended runs nothing again: its chain refuses a retry (409), so none is offered (R8b-06).
  const state = ENDED.has(item.display_status ?? "") ? null : shown;
  const [confirm, setConfirm] = useState<"skip" | "retry" | null>(null);
  const [steer, setSteer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (p: ReturnType<typeof act.pause>) => {
    setBusy(true);
    setError(null);
    const r = await p;
    setBusy(false);
    if (!r.ok) return setError(r.error);
    setConfirm(null);
    setSteer("");
    reload();
  };
  const retry = () => run(act.retry(item.id, { path, ...(steer.trim() ? { steer: steer.trim() } : {}) }));
  const offerRetry = (state === "paused" || state === "stopped") && retryable(item);
  if (!extra && state !== "running" && state !== "paused" && !offerRetry) return null;
  if (confirm === "skip")
    return (
      <div className="ip-confirm" role="group" aria-label={`Skip ${path}?`}>
        <p className="ip-confirm-q">Skip <code>{path}</code>?</p>
        <p className="item-muted">{what === "task" ? "The task stops and does not run again this round." : what === "step" ? "Every task in the step stops; the node goes on with the next step." : "The node stops here; the chain goes on with the next node."}</p>
        {error && <p className="item-error" role="alert">{error}</p>}
        <div className="item-actions">
          <Button variant="danger" disabled={busy} onClick={() => run(act.skip(item.id, path))}>Skip</Button>
          <Button onClick={() => setConfirm(null)}>Cancel</Button>
        </div>
      </div>
    );
  if (confirm === "retry")
    return (
      <div className="ip-confirm" role="group" aria-label={`Retry ${path}`}>
        <p className="ip-confirm-q">Retry <code>{path}</code></p>
        {item.steerable !== false && <textarea aria-label="Steer for the retry" className="item-input" rows={2} placeholder="Steer the next attempt (optional)" value={steer} onChange={(e) => setSteer(e.target.value)} onKeyDown={sendOnModEnter(retry, !busy)} />}
        {error && <p className="item-error" role="alert">{error}</p>}
        <div className="item-actions">
          <Button variant="primary" disabled={busy} onClick={retry}>Retry</Button>
          <Button onClick={() => setConfirm(null)}>Cancel</Button>
        </div>
      </div>
    );
  return (
    <div className="ip-footer">
      {extra}
      {state === "running" && <Button disabled={busy} onClick={() => run(act.pause(item.id))}>Pause</Button>}
      {state === "paused" && <Button disabled={busy} onClick={() => run(act.resume(item.id))}>Resume</Button>}
      {(state === "running" || state === "paused") && <Button onClick={() => setConfirm("skip")}>Skip {what}</Button>}
      {offerRetry && <Button onClick={() => setConfirm("retry")}>Retry</Button>}
      {error && <span className="item-error" role="alert">{error}</span>}
    </div>
  );
}
