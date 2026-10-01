import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { CommentReview, FixTarget, GatelessChanges, ReviewOutcome, ReviewThread, WorkItem } from "../../types";
import { detailOf, jsonBody, request } from "../http";
import { placeUrl } from "../item/url";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { showToast } from "../ui/Toast";
import { approveBlock, barText, drafts, outcomes } from "./finish";

type Item = Pick<WorkItem, "id" | "pending_gate" | "display_status" | "fix_target">;
const ACTION = { retried: "retried", rerun: "running again", queued: "queued" } as const;

/** Sends a review and moves on (G.5, G.6): a refusal comes back as the server's words. */
export function useSubmit(item: Item, gate: string | null, threads: ReviewThread[], reload: () => void) {
  const navigate = useNavigate();
  return async (outcome: ReviewOutcome, summary: string): Promise<string | null> => {
    const atGate = !!gate && item.pending_gate === gate;
    const path = atGate ? `/work-items/${encodeURIComponent(item.id)}/gates/${encodeURIComponent(gate!)}/review` : `/work-items/${encodeURIComponent(item.id)}/review`;
    const n = drafts(threads).length;
    const { status, body } = await request(path, jsonBody("POST", { outcome, ...(summary.trim() && { summary: summary.trim() }) }));
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
export function BottomBar({ item, gate, threads, onFinish, submit }: { item: Item; gate: string | null; threads: ReviewThread[]; onFinish: (o?: ReviewOutcome) => void; submit: ReturnType<typeof useSubmit> }) {
  const [error, setError] = useState<string | null>(null);
  const bar = barText(threads, item, gate);
  const block = approveBlock(item, gate, threads);
  return (
    <div className="rv-bar">
      <span className="rv-bar-title">{bar.title}</span>
      <span className="rv-muted">{bar.text}</span>
      {bar.agent && <><span aria-hidden="true" className="rv-muted">·</span><span className="rv-muted">{bar.agent}</span></>}
      {error && <span className="rv-error" role="alert">{error}</span>}
      <span className="rv-spacer" />
      {bar.published ? (
        <>
          <Button onClick={() => onFinish("request_changes")}>Request changes</Button>
          <Button variant="primary" disabled={!!block} title={block ?? undefined} onClick={async () => setError(await submit("approve", ""))}>Approve</Button>
        </>
      ) : (
        <Button variant="primary" onClick={() => onFinish()}>Finish review</Button>
      )}
    </div>
  );
}

/** Finish your review (prototype 505–526). */
export function FinishDialog({ item, gate, threads, initial, submit, onClose }: { item: Item; gate: string | null; threads: ReviewThread[]; initial?: ReviewOutcome; submit: ReturnType<typeof useSubmit>; onClose: () => void }) {
  const atGate = !!gate && item.pending_gate === gate;
  const fix = useFixTarget(item, atGate, true);
  const rows = outcomes(item, gate, threads, fix);
  const firstOpen = rows.find((r) => !r.disabled)?.key ?? "comment";
  const [picked, setPicked] = useState<ReviewOutcome | null>(initial ?? null);
  const outcome = picked && !rows.find((r) => r.key === picked)?.disabled ? picked : firstOpen;
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const toSend = drafts(threads);
  const canSend = outcome === "approve" || toSend.length > 0 || !!note.trim();
  const go = async () => {
    setBusy(true);
    const e = await submit(outcome, note);
    setBusy(false);
    if (e) setError(e);
    else onClose();
  };
  return (
    <Dialog
      title="Finish your review"
      onClose={onClose}
      dirty={!!note.trim()}
      footer={
        <>
          <Button onClick={onClose}>Keep reviewing</Button>
          <Button variant="primary" disabled={!canSend || busy} title={canSend ? undefined : "Add a comment or a note first"} onClick={go}>Submit review</Button>
        </>
      }
    >
      <div className="rv-finish">
        <ul className="rv-finish-list" aria-label="To send">
          {toSend.map(({ thread: t, comment: c }) => (
            <li key={c.id}>
              {t.label && <span className={`rv-tag is-${t.label}`}>{{ must_fix: "MUST FIX", question: "QUESTION", nit: "NIT" }[t.label]}</span>}
              <span className="rv-mono rv-muted">{t.file_path ?? "item"}{t.end_line !== null ? `:${t.end_line}` : ""}</span>
              <span className="rv-finish-text">{c.body}</span>
            </li>
          ))}
          {!toSend.length && <li className="rv-muted">No threads yet. Add a comment on a line or a file first.</li>}
        </ul>
        <textarea className="rv-textarea" aria-label="Overall note" placeholder="Overall note for the agent (optional), markdown supported" value={note} onChange={(e) => setNote(e.target.value)} />
        <fieldset className="rv-outcomes">
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
        </fieldset>
        {error && <p className="rv-error" role="alert">{error}</p>}
      </div>
    </Dialog>
  );
}
