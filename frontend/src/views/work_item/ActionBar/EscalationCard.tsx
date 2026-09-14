import { useEffect, useState } from "react";
import * as api from "../../../api";
import type { KraftEvent, WorkerSession, WorkItem } from "../../../types";

/** 06's escalating pill: "● Agent is on it · turn N", or "● Auto-escalated ·
 *  turn N" when the turn fired unattended (Kraft-vyk8). `thread` only shows
 *  once there is more than one (Kraft-dkb6g) -- the common case, a single
 *  thread, reads exactly as it always has. The pill only: Stop escalation
 *  lives in the card's More actions (W11 · J). */
export function EscalatingPill({
  turn,
  auto,
  thread,
}: {
  turn: number;
  auto?: boolean;
  thread?: number;
}) {
  const threadPart = thread && thread > 1 ? `thread ${thread} · ` : "";
  return (
    <span className="tag tag-accent escalating-pill" data-testid="escalating-pill">
      {auto ? `● Auto-escalated · ${threadPart}turn ${turn}` : `● Agent is on it · ${threadPart}turn ${turn}`}
    </span>
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
 *  a summary -- say why it actually stopped (Stop agent leaves the session
 *  `paused`; a failure is a failure). A clean finish has no reason to give. */
function stopReasonText(session: WorkerSession): string | null {
  if (session.status === "paused") return "stopped by Stop agent";
  if (session.status === "failed") return "turn failed";
  return null;
}

/** What an escalated turn reported: `summary` is the agent's own words (safe
 *  to feed back as a steer), `text` is what the card shows -- the summary, or
 *  the turn's message and why it stopped. No session, no report. */
export function useEscalationReport(
  item: WorkItem,
  session: WorkerSession | undefined,
  events: KraftEvent[],
): { summary: string | null; text: string } {
  const exitEv = session
    ? events.find((e) => e.type === "worker_session_exited" && e.payload.session_id === session.id)
    : undefined;
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
  const sessionId = session?.id;
  const summaryRef = session?.session_summary_ref;
  useEffect(() => {
    setDocSummary(null);
    if (!sessionId || exitSummary || !summaryRef) return;
    let live = true;
    api
      .getWorkItemDocuments(item.id)
      .then((res) => {
        const doc = res.documents.find((d) => d.worker_session_id === sessionId);
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
  }, [item.id, sessionId, summaryRef, exitSummary]);
  if (!session) return { summary: null, text: "" };
  // Only an actual summary is safe to feed back in as a steer -- the stop-
  // reason fallback below is our own placeholder text, not something the
  // agent said.
  const summary = exitSummary ?? docSummary;
  // No summary: quote what this turn was sent (W8.6) -- "no summary
  // reported" only when there is no message either.
  const asked = events.find(
    (e) => e.type === "escalation_message" && e.payload.session_id === session.id,
  )?.payload.message as string | undefined;
  const text =
    summary ??
    ([asked && `> ${asked}`, stopReasonText(session)].filter(Boolean).join("\n\n") ||
      "no summary reported");
  return { summary, text };
}
