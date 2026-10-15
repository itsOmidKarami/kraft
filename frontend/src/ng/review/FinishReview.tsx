import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { CommentReview, FixTarget, GatelessChanges, ReviewOutcome, ReviewThread, WorkItem } from "../../types";
import { detailOf, jsonBody, request } from "../http";
import { placeUrl } from "../item/url";
import { Button } from "../ui/Button";
import { useReason } from "../ui/Reason";
import { ADD_NOTE, ADD_TO_SUBMIT } from "../ui/reasons";
import { Dialog } from "../ui/Dialog";
import { showToast } from "../ui/Toast";
import { REQUEST_CHANGES, approveBlock, barText, drafts, outcomes } from "./finish";
import { rangeName, threadRange } from "./Thread";
import { sendOnModEnter } from "../keys";

type Item = Pick<WorkItem, "id" | "pending_gate" | "display_status" | "fix_target">;
const ACTION = { retried: "retried", rerun: "running again", queued: "queued" } as const;

/** Sends a review and moves on (G.5, G.6): a refusal comes back as the server's words. */
/** `digest`: the chain revision's, as GET /artifact returned it; an approval at
 *  a chain-revision gate carries it (Kraft-ec66w, #355). */
/** A thread's lines as its composer and its card name them: " · lines +3 to +5". */
const where = (t: ReviewThread) => {
  const r = threadRange(t);
  return r ? ` · ${rangeName(r).toLowerCase()}` : "";
};

export function useSubmit(item: Item, gate: string | null, threads: ReviewThread[], reload: () => void, digest?: string | null) {
  const navigate = useNavigate();
  return async (outcome: ReviewOutcome, summary: string): Promise<string | null> => {
    const atGate = !!gate && item.pending_gate === gate;
    const path = atGate ? `/work-items/${encodeURIComponent(item.id)}/gates/${encodeURIComponent(gate!)}/review` : `/work-items/${encodeURIComponent(item.id)}/review`;
    const n = drafts(threads).length;
    const { status, body } = await request(path, jsonBody("POST", { outcome, ...(summary.trim() && { summary: summary.trim() }), ...(outcome === "approve" && digest && { digest }) }));
    if (status < 200 || status >= 300) return detailOf(body);
    const threadsSent = `${n} ${n === 1 ? "thread" : "threads"}`;
    if (outcome === "comment") {
      reload();
      showToast(`${threadsSent} sent · ${(body as CommentReview).gate ? "gate stays open" : "the item keeps going"}`);
      return null;
    }
    if (outcome === "approve") {
      showToast(`Approved · ${gate} passed`);
      navigate(placeUrl(item.id, { sel: { kind: "chain" } }));
      return null;
    }
    const gateless = !atGate ? (body as GatelessChanges) : null;
    const target = gateless?.target ?? item.fix_target?.node ?? null;
    showToast(`Requested changes · ${threadsSent} sent to ${target ?? "the fix node"}${gateless ? ` · ${ACTION[gateless.action] ?? gateless.action}` : ""}`);
    navigate(placeUrl(item.id, { sel: target ? { kind: "node", node: target } : { kind: "chain" } }));
    return null;
  };
}

/** Where request-changes would send the work: the detail's `fix_target` at a
 *  pending gate, else `GET /fix-target` (read when the dialog opens). */
function useFixTarget(item: Item, atGate: boolean, open: boolean) {
  const [gateless, setGateless] = useState<FixTarget | null>(null);
  useEffect(() => {
    if (atGate || !open) return;
    let live = true;
    request<FixTarget>(`/work-items/${encodeURIComponent(item.id)}/fix-target`).then(({ status, body }) => live && status === 200 && setGateless(body));
    return () => void (live = false);
  }, [item.id, atGate, open]);
  return atGate ? item.fix_target ?? null : gateless;
}

/** The row under the diff (prototype 496–503). */
export function BottomBar({ item, gate, threads, readOnly, onFinish, submit }: { item: Item; gate: string | null; threads: ReviewThread[]; readOnly?: boolean; onFinish: (o?: ReviewOutcome) => void; submit: ReturnType<typeof useSubmit> }) {
  const [error, setError] = useState<string | null>(null);
  const bar = barText(threads, item, gate);
  const atGate = !!gate && item.pending_gate === gate;
  const block = approveBlock(item, gate, threads);
  const why = useReason(block);
  if (readOnly)
    return (
      <div className="rv-bar">
        <span className="rv-bar-title">Read only</span>
        <span className="rv-muted">This item has ended; its review is closed to new comments.</span>
      </div>
    );
  return (
    <div className="rv-bar">
      <span className="rv-bar-title">{bar.title}</span>
      <span className="rv-muted">{bar.text}</span>
      {bar.agent && <><span aria-hidden="true" className="rv-muted">·</span><span className="rv-muted">{bar.agent}</span></>}
      {error && <span className="rv-error" role="alert">{error}</span>}
      <span className="rv-spacer" />
      {atGate && (
        <>
          <Button onClick={() => onFinish("request_changes")}>{REQUEST_CHANGES}</Button>
          {/* With comments pending, Approve would send them as the gate passes, where no agent acts on
              them: it opens Finish your review on Approve instead, which lists them first. */}
          {why.note}
          <Button variant={bar.pending ? undefined : "primary"} disabled={!!block} {...why.attrs} onClick={async () => (bar.pending ? onFinish("approve") : setError(await submit("approve", "")))}>Approve</Button>
        </>
      )}
      {bar.pending > 0 && <Button variant="primary" onClick={() => onFinish()}>Finish review</Button>}
    </div>
  );
}

/** Finish your review (prototype 505–526). */
export function FinishDialog({ item, gate, threads, initial, submit, onClose }: { item: Item; gate: string | null; threads: ReviewThread[]; initial?: ReviewOutcome; submit: ReturnType<typeof useSubmit>; onClose: () => void }) {
  const atGate = !!gate && item.pending_gate === gate;
  const fix = useFixTarget(item, atGate, true);
  const rows = outcomes(item, gate, threads, fix);
  // With no threads there is nothing to send back or comment on: the one thing left to do is approve.
  const approveFirst = !threads.length && !rows.find((r) => r.key === "approve")?.disabled;
  const firstOpen = approveFirst ? "approve" : rows.find((r) => !r.disabled)?.key ?? "comment";
  const [picked, setPicked] = useState<ReviewOutcome | null>(initial ?? null);
  // Opened on Request changes there is one action to take: the choice of outcomes would only offer it again, with Approve beside it.
  const request = rows[0];
  const focused = initial === "request_changes" && !request.disabled;
  const outcome = focused ? "request_changes" : picked && !rows.find((r) => r.key === picked)?.disabled ? picked : firstOpen;
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const noteBox = useRef<HTMLTextAreaElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The fix target can arrive after the dialog opens; the note takes focus when it does.
  useEffect(() => { if (focused) noteBox.current?.focus(); }, [focused]);
  const toSend = drafts(threads);
  const canSend = outcome === "approve" || toSend.length > 0 || !!note.trim();
  // Said after a press that had nothing to send, and gone once there is something.
  const noSend = useReason(tried && !canSend ? (focused ? ADD_NOTE : ADD_TO_SUBMIT) : null);
  const go = async () => {
    if (!canSend) {
      setTried(true);
      noteBox.current?.focus();
      return;
    }
    setBusy(true);
    const e = await submit(outcome, note);
    setBusy(false);
    if (e) setError(e);
    else onClose();
  };
  return (
    <Dialog
      title={focused ? REQUEST_CHANGES : "Finish your review"}
      onClose={onClose}
      dirty={!!note.trim()}
      footer={
        <>
          <Button onClick={onClose}>Keep reviewing</Button>
          {noSend.note}
          <Button variant="primary" disabled={busy} onClick={go}>{outcome === "request_changes" ? REQUEST_CHANGES : "Submit review"}</Button>
        </>
      }
    >
      <div className="rv-finish">
        {focused && <p className="rv-muted rv-finish-sends">{request.parts.map((p, i) => <span key={i} className={p.mono ? "rv-mono" : undefined}>{p.t}</span>)}</p>}
        {(toSend.length > 0 || !focused) && <ul className="rv-finish-list" aria-label="To send">
          {toSend.map(({ thread: t, comment: c }) => (
            <li key={c.id}>
              {t.label && <span className={`rv-tag is-${t.label}`}>{{ must_fix: "MUST FIX", question: "QUESTION", nit: "NIT" }[t.label]}</span>}
              {/* A thread on no file is "on the whole change", as the page heads it (R10b-06). */}
              <span className="rv-mono rv-muted">{t.file_path ?? "the whole change"}{where(t)}</span>
              <span className="rv-finish-text">{c.body}</span>
            </li>
          ))}
          {!toSend.length && <li className="rv-muted">No threads yet. Add a comment on a line or a file first.</li>}
        </ul>}
        <textarea ref={noteBox} className="rv-textarea" aria-label="Overall note" {...noSend.attrs} placeholder={focused && !toSend.length ? "What needs to change? The agent reads this first, markdown supported" : "Overall note for the agent (optional), markdown supported"} value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={sendOnModEnter(go, !busy)} />
        {noSend.note}
        {!focused && <fieldset className="rv-outcomes">
          <legend className="review-visually-hidden">Outcome</legend>
          {rows.map((r) => (
            <label key={r.key} className={`rv-outcome${r.disabled ? " is-off" : ""}${outcome === r.key ? " is-on" : ""}`}>
              <input type="radio" name="outcome" value={r.key} checked={outcome === r.key} disabled={r.disabled} onChange={() => setPicked(r.key)} />
              <span className="rv-outcome-text">
                <span className="rv-outcome-label">{r.label}</span>
                <span className="rv-outcome-sub">{r.parts.map((p, i) => <span key={i} className={p.mono ? "rv-mono" : undefined}>{p.t}</span>)}</span>
              </span>
            </label>
          ))}
        </fieldset>}
        {error && <p className="rv-error" role="alert">{error}</p>}
      </div>
    </Dialog>
  );
}
