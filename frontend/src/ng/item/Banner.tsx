import { useState } from "react";
import { CircleHelp } from "../icons";
import { Button } from "../ui/Button";
import { act } from "./actions";
import { taskName } from "./paths";
import type { ItemDetail } from "./useItem";

/** The reason banner (Decisions §4): only when the item needs you at a gate
 *  or a cap; the action is the filled button. */
export function Banner({ item, onOpenGate, onRaise }: { item: ItemDetail; onOpenGate: (gate: string) => void; onRaise: () => void }) {
  const stop = item.stop;
  if (item.display_status !== "needs_you" || !stop) return null;
  if (stop.kind === "gate") {
    const gate = item.pending_gate ?? stop.node ?? "";
    return (
      <div className="item-banner" role="status">
        <span aria-hidden className="item-banner-glyph">✦</span>
        <span className="item-banner-text">Waiting for your approval at <code>{gate}</code>.</span>
        <Button variant="primary" onClick={() => onOpenGate(gate)}>Open gate</Button>
      </div>
    );
  }
  if (stop.kind === "cap" || stop.kind === "budget")
    return (
      <div className="item-banner" role="status">
        <span aria-hidden className="item-banner-glyph">✦</span>
        <span className="item-banner-text">
          {(stop.reason ?? (stop.kind === "budget" ? "The budget ran out" : "A limit was reached")).replace(/\.$/, "")}
          {stop.node && <> at <code>{stop.node}</code></>}.
        </span>
        <Button variant="primary" onClick={onRaise}>Raise cap</Button>
      </div>
    );
  return null;
}

/** An agent's question (Decisions §4, prototype lines 93–100): the card under
 *  the brief, or one line in a node view, whose Open thread goes to the thread. */
export function QuestionCard({ item, compact, reload, onOpenThread }: { item: ItemDetail; compact: boolean; reload: () => void; onOpenThread: () => void }) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const q = item.needs_context_question;
  if (item.display_status !== "needs_you" || item.stop?.kind !== "question" || !q) return null;
  const where = item.stop.node;
  if (compact)
    return (
      <div className="item-banner is-question" role="status">
        <span className="item-banner-text item-one-line">
          {item.stop.task ? `${taskName(item.stop.task)} is` : "The agent is"} asking you{where && <> on <code>{where}</code></>}: “{q}”
        </span>
        <Button variant="primary" onClick={onOpenThread}>Open thread</Button>
      </div>
    );
  return (
    <section className="item-card is-warn" aria-label="Needs you">
      <h2 className="item-card-head">
        <CircleHelp size={14} aria-hidden /> <span className="item-card-title">Needs you</span>
        <span className="item-card-where">asked by {item.stop.task ? taskName(item.stop.task) : "the agent"}{where && ` · on ${where}`}</span>
      </h2>
      <blockquote className="item-quote">“{q}”</blockquote>
      <label className="item-visually-hidden" htmlFor="item-answer">Your answer</label>
      <textarea id="item-answer" className="item-input" rows={2} placeholder="Or write an answer…" value={answer} onChange={(e) => setAnswer(e.target.value)} />
      {error && <p className="item-error" role="alert">{error}</p>}
      <div className="item-actions">
        <Button variant="primary" disabled={busy || !answer.trim()} onClick={async () => {
          setBusy(true);
          const r = await act.resume(item.id, answer.trim());
          setBusy(false);
          if (!r.ok) return setError(r.error);
          setAnswer("");
          reload();
        }}>Send &amp; resume</Button>
        <Button onClick={onOpenThread}>Open thread</Button>
      </div>
    </section>
  );
}
