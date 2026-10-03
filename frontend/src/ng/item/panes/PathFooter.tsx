import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Button } from "../../ui/Button";
import { act } from "../actions";
import type { FooterState } from "../nodeGraph";
import type { ItemDetail } from "../useItem";
import { holdsText, isEscape, sendOnModEnter } from "../../keys";
import { pausable, retryable, skippable } from "../status";

const ENDED = new Set(["done", "cancelled", "archived"]);

export type FooterAct = "pause" | "resume" | "skip" | "retry";

/** What a path's footer offers, in order: each only where the server takes it.
 *  Pause where `/pause` would (`pausable`: never on a stopped item, R11b-01),
 *  Resume on a paused one, Skip where `/skip` would (not past a live walk), and Retry only on an item
 *  `/retry` claims (`retryable`): a paused one has Resume, and a running or
 *  waiting one nothing (R10b-01). An ended item runs nothing again (R8b-06). */
export function footerActs(item: ItemDetail, shown: FooterState): FooterAct[] {
  const state = ENDED.has(item.display_status ?? "") ? null : shown;
  return [
    ...(state === "running" && pausable(item) ? ["pause" as const] : []),
    ...(state === "paused" ? ["resume" as const] : []),
    // Skip on a live footer only while the item runs: a stopped one with a live session (a gate's auto-review) has a walk /skip refuses.
    ...(((state === "running" && pausable(item)) || state === "paused") && skippable(item) ? ["skip" as const] : []),
    ...((state === "paused" || state === "stopped") && retryable(item) ? ["retry" as const] : []),
  ];
}

/** A node's, step's or task's footer (Decisions §5 Pause on a path, §6 Skip):
 *  Pause, Skip and Retry in that order, by state (`footerActs`). Skip and Retry
 *  confirm in the pane; Retry takes a steer when the item has an agent to read
 *  it. A confirm takes the focus when it opens (its steer, or its least
 *  destructive button), and Cancel or Escape hands it back to the button that
 *  opened it (R11b-03); Escape in a steer with text in it keeps the text. */
export function PathFooter({ item, path, what, state, reload, extra, only }: { item: ItemDetail; path: string; what: "node" | "step" | "task"; state: FooterState; reload: () => void; extra?: React.ReactNode; only?: FooterAct }) {
  const acts = footerActs(item, state).filter((x) => !only || x === only);
  const [confirm, setConfirm] = useState<"skip" | "retry" | null>(null);
  const [steer, setSteer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const opener = useRef<{ skip: HTMLButtonElement | null; retry: HTMLButtonElement | null }>({ skip: null, retry: null });
  const back = useRef<"skip" | "retry" | null>(null);
  useEffect(() => {
    if (confirm === null && back.current) opener.current[back.current]?.focus();
    back.current = null;
  }, [confirm]);
  const close = () => {
    back.current = confirm;
    setConfirm(null);
  };
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
  // The confirm's own Escape: it closes the confirm, never the pane, and keeps a steer that has text.
  const onKey = (e: KeyboardEvent) => {
    if (!isEscape(e)) return;
    e.preventDefault();
    e.stopPropagation();
    if (!holdsText(e.target)) close();
  };
  if (!extra && !acts.length) return null;
  if (confirm === "skip")
    return (
      <div className="ip-confirm" role="group" aria-label={`Skip ${path}?`} onKeyDown={onKey}>
        <p className="ip-confirm-q">Skip <code>{path}</code>?</p>
        <p className="item-muted">{what === "task" ? "The task stops and does not run again this round." : what === "step" ? "Every task in the step stops; the node goes on with the next step." : "The node stops here; the chain goes on with the next node."}</p>
        {error && <p className="item-error" role="alert">{error}</p>}
        <div className="item-actions">
          <Button variant="danger" disabled={busy} onClick={() => run(act.skip(item.id, path))}>Skip</Button>
          <Button autoFocus onClick={close}>Cancel</Button>
        </div>
      </div>
    );
  if (confirm === "retry") {
    const steerable = item.steerable !== false;
    return (
      <div className="ip-confirm" role="group" aria-label={`Retry ${path}`} onKeyDown={onKey}>
        <p className="ip-confirm-q">Retry <code>{path}</code></p>
        {steerable && <textarea autoFocus aria-label="Steer for the retry" className="item-input" rows={2} placeholder="Steer the next attempt (optional)" value={steer} onChange={(e) => setSteer(e.target.value)} onKeyDown={sendOnModEnter(retry, !busy)} />}
        {error && <p className="item-error" role="alert">{error}</p>}
        <div className="item-actions">
          <Button autoFocus={!steerable} variant="primary" disabled={busy} onClick={retry}>Retry</Button>
          <Button onClick={close}>Cancel</Button>
        </div>
      </div>
    );
  }
  return (
    <div className="ip-footer">
      {extra}
      {acts.includes("pause") && <Button disabled={busy} onClick={() => run(act.pause(item.id))}>Pause</Button>}
      {acts.includes("resume") && <Button disabled={busy} onClick={() => run(act.resume(item.id))}>Resume</Button>}
      {acts.includes("skip") && <Button ref={(el) => void (opener.current.skip = el)} onClick={() => setConfirm("skip")}>Skip {what}</Button>}
      {acts.includes("retry") && <Button ref={(el) => void (opener.current.retry = el)} onClick={() => setConfirm("retry")}>Retry</Button>}
      {error && <span className="item-error" role="alert">{error}</span>}
    </div>
  );
}
