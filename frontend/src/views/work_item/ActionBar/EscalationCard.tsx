import { useEffect, useState } from "react";
import { Robot } from "@phosphor-icons/react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import * as api from "../../../api";
import type { KraftEvent, WorkerSession, WorkItem } from "../../../types";
import { useActionBar } from "./useActionBar";

/** 06's escalating pill: "● Agent is on it · turn N" + Stop agent, the
 *  Steer & retry / Escalate buttons visibly disabled ("one escalation turn
 *  at a time") -- or, when the turn fired unattended, "● Auto-escalated ·
 *  turn N" with a hint saying why (Kraft-vyk8). `auto` absent or false
 *  renders today's copy unchanged. */
export function EscalatingPill({
  turn,
  auto,
  onStop,
  busy,
  err,
}: {
  turn: number;
  auto?: boolean;
  onStop: () => void;
  busy: boolean;
  err?: string | null;
}) {
  return (
    <div className="control-row escalating-pill" data-testid="escalating-pill">
      <span className="tag tag-accent">
        {auto ? `● Auto-escalated · turn ${turn}` : `● Agent is on it · turn ${turn}`}
      </span>
      <button className="btn btn-secondary" disabled={busy} onClick={onStop}>
        Stop agent
      </button>
      <button className="btn btn-ghost" disabled>
        Steer & retry
      </button>
      <button className="btn btn-ghost" disabled>
        Escalate
      </button>
      <span className="control-hint">
        {err ??
          (auto
            ? "fired automatically — nobody had acted on it yet"
            : "one escalation turn at a time")}
      </span>
    </div>
  );
}

const DISMISS_KEY = (itemId: string) => `kraft:dismissed-escalation:${itemId}`;

/** No server field carries "this escalation card was dismissed" -- it's a
 *  client-side-only fact (Prototype `dismissEsc`), so it lives in
 *  localStorage keyed by item, same shape a per-browser draft would use. */
export function dismissedTurnId(itemId: string): string | null {
  try {
    return localStorage.getItem(DISMISS_KEY(itemId));
  } catch {
    return null;
  }
}
export function dismissTurn(itemId: string, sessionId: string): void {
  try {
    localStorage.setItem(DISMISS_KEY(itemId), sessionId);
  } catch {
    /* private mode */
  }
}

/** No `concerns`/`question` on the exit event means the turn never reported
 *  a summary -- fall back to why it actually stopped (Stop agent leaves the
 *  session `paused`; anything else that isn't a clean report is a failure)
 *  rather than a bare "no summary reported" that reads as the agent's own
 *  words. */
function stopReasonText(session: WorkerSession): string {
  if (session.status === "paused") return "stopped by Stop agent";
  if (session.status === "failed") return "turn failed";
  return "no summary reported";
}

/** The escalated proposal card: "Escalation · turn N · reported", the
 *  agent's own summary (off its exit event), Apply as steer & retry ·
 *  Reply · Dismiss. */
export function EscalatedCard({
  item,
  session,
  events,
  onOpenReply,
  onDismiss,
}: {
  item: WorkItem;
  session: WorkerSession;
  events: KraftEvent[];
  onOpenReply: () => void;
  onDismiss: () => void;
}) {
  const { busy, err, run } = useActionBar(item.id);
  const exitEv = events.find(
    (e) =>
      e.type === "worker_session_exited" && e.payload.session_id === session.id,
  );
  const exitSummary =
    (exitEv?.payload.concerns as string | undefined) ??
    (exitEv?.payload.question as string | undefined) ??
    null;
  // Escalation turns get the standard worker result contract (adapters/
  // agent.py _CTX), so a turn that finishes clean with status "done" carries
  // neither `concerns` nor `question` on its exit event -- it still reported,
  // just via `session_summary_ref` instead. Read that document rather than
  // treating a clean finish as "no summary reported".
  const [docSummary, setDocSummary] = useState<string | null>(null);
  useEffect(() => {
    setDocSummary(null);
    if (exitSummary || !session.session_summary_ref) return;
    let live = true;
    api
      .getWorkItemDocuments(item.id)
      .then((res) => {
        const doc = res.documents.find((d) => d.worker_session_id === session.id);
        if (!doc) return null;
        return api.getDocument(doc.document_id);
      })
      .then((d) => {
        if (live && d) setDocSummary(d.content.trim() || null);
      })
      .catch(() => {
        /* no summary document available -- fall back below */
      });
    return () => {
      live = false;
    };
  }, [item.id, session.id, session.session_summary_ref, exitSummary]);
  // Only an actual summary is safe to feed back in as a steer -- the stop-
  // reason fallback below is our own placeholder text, not something the
  // agent said.
  const summary = exitSummary ?? docSummary;
  const text = summary ?? stopReasonText(session);
  return (
    <div className="card attention-card" data-testid="escalated-card">
      <div className="attention-head">
        <Robot size={18} className="attention-glyph" />
        <div className="attention-text">
          <span className="attention-title">
            Escalation · turn {session.attempt} · reported
          </span>
          {/* `attention-sub` used to be a <span>, but markdown emits block
             elements (react-markdown always does), so it has to be a <div>
             or React warns about a <p> inside a <span>. Rendering makes the
             card taller, so it gets a max-height + scroll rather than
             pushing the buttons below off screen. */}
          <div className="attention-sub doc-modal-body attention-sub-md">
            <Markdown remarkPlugins={[remarkGfm]}>{text}</Markdown>
          </div>
        </div>
      </div>
      <div className="gate-actions">
        {summary && (
          <button
            className="btn btn-primary"
            disabled={busy}
            onClick={() =>
              run(
                () => api.retryWorkItem(item.id, `From escalation: ${summary}`),
                "Applied as steer — retrying",
              )
            }
          >
            Apply as steer & retry
          </button>
        )}
        <button
          className="btn btn-secondary"
          disabled={busy}
          onClick={onOpenReply}
        >
          Reply
        </button>
        <button
          className="btn btn-ghost"
          disabled={busy}
          onClick={() => {
            dismissTurn(item.id, session.id);
            onDismiss();
          }}
        >
          Dismiss
        </button>
      </div>
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}
