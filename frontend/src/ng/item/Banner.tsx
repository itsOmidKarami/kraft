import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { gateReviewUrl } from "../review/url";
import { CircleHelp } from "../icons";
import { Button } from "../ui/Button";
import { act } from "./actions";
import { taskName } from "./paths";
import { RaiseLimit, useLimitAsked } from "./RaiseLimit";
import { budgetRaise, NOT_RAISABLE } from "./status";
import type { ItemDetail } from "./useItem";
import { sendOnModEnter } from "../keys";

/** A stop's reason as a sentence of its own: a capital first letter and a full stop. */
const sentence = (s: string) => `${s.charAt(0).toUpperCase()}${s.slice(1).replace(/\.*$/, "")}.`;

/** The reason banner (Decisions §4): only when the item needs you at a gate
 *  or a cap; the action is the filled button. A gate's is Review changes, its review (GR-5). */
export function Banner({ item, onRaise, reload, note }: { item: ItemDetail; onRaise: () => void; reload: () => void; note?: ReactNode }) {
  const navigate = useNavigate();
  const [raising, setRaising] = useState(false);
  useLimitAsked(item.id, () => setRaising(true));
  const stop = item.stop;
  if (item.display_status !== "needs_you" || !stop) return null;
  if (stop.kind === "gate") {
    const gate = item.pending_gate ?? stop.node ?? "";
    return (
      <div className="item-banner" role="status">
        <span aria-hidden className="item-banner-glyph">✦</span>
        <span className="item-banner-text">Waiting for your approval at <code>{gate}</code>.{note && <span className="item-banner-note">{note}</span>}</span>
        <Button variant="primary" onClick={() => navigate(gateReviewUrl(item.id, gate))}>Review changes</Button>
      </div>
    );
  }
  if (stop.kind === "cap" || stop.kind === "budget")
    return (
      <div className="item-banner" role="status">
        <span aria-hidden className="item-banner-glyph">✦</span>
        <span className="item-banner-text">
          {/* Where first: the reason can be two sentences, and a node tacked on after them read as part of the last. */}
          {stop.node && <>Stopped at <code>{stop.node}</code>. </>}
          {sentence(stop.reason ?? (stop.kind === "budget" ? "The budget ran out" : "A limit was reached"))}
          {stop.kind === "budget" && !budgetRaise(item) && <> {NOT_RAISABLE}</>}
        </span>
        {/* A cap that names its limit opens that limit's editor; any other cap stop opens the Config it can only point at,
            and a budget stop on the item's own cap that Config's budget editor. A budget the item cannot raise says so, and the header's Retry is the way on. */}
        {stop.limit
          ? <Button variant="primary" onClick={() => setRaising(true)}>Raise cap</Button>
          : stop.kind === "cap" ? <Button variant="primary" onClick={onRaise}>Open config</Button>
          : budgetRaise(item) && <Button variant="primary" onClick={onRaise}>Raise cap</Button>}
        {raising && stop.limit && <RaiseLimit itemId={item.id} limit={stop.limit} override={item.policy_override} onClose={() => setRaising(false)} onDone={() => { setRaising(false); reload(); }} />}
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
  const send = async () => {
    setBusy(true);
    const r = await act.resume(item.id, answer.trim());
    setBusy(false);
    if (!r.ok) return setError(r.error);
    setAnswer("");
    reload();
  };
  if (item.display_status !== "needs_you" || item.stop?.kind !== "question" || !q) return null;
  const where = item.stop.node;
  if (compact)
    return (
      <div className="item-banner is-question" role="status">
        {/* Decisions §4: in a node view the question is one line; the whole of it is in the title (README allowlist #10). */}
        <span className="item-banner-text item-one-line" data-allow-ellipsis="" title={q}>
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
      <textarea aria-label="Your answer" className="item-input" rows={2} placeholder="Or write an answer…" value={answer} onChange={(e) => setAnswer(e.target.value)} onKeyDown={sendOnModEnter(send, !busy && !!answer.trim())} />
      {error && <p className="item-error" role="alert">{error}</p>}
      <div className="item-actions">
        <Button variant="primary" disabled={busy || !answer.trim()} onClick={send}>Send &amp; resume</Button>
        <Button onClick={onOpenThread}>Open thread</Button>
      </div>
    </section>
  );
}
