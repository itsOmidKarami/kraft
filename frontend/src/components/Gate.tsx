import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Check, Flag } from "@phosphor-icons/react";
import * as api from "../api";
import { Escalate } from "./Escalate";
import type { Finding, WorkerSession, WorkItem } from "../types";

/**
 * The gate prompt, in the two places a gate is offered: the board's inline row
 * under a needs-you item (2a) and the detail's attention card (4a).
 *
 * Rejection always carries a note — the reject action swaps the button row for
 * a textarea, and stays disabled until the note is non-empty (spec §5).
 */

/** One line per gate in the shipped templates; anything else gets the fallback. */
const PROMPTS: Record<string, string> = {
  spec_approval: "approve the spec to continue",
  plan_approval: "approve the plan to continue",
  chain_finalized: "approve the revised chain to continue",
  human_review_approval: "approve the merge request to continue",
};

/**
 * What the gate's artifact *is*, per gate. A map rather than a ternary on
 * `gate`: a new artifact-carrying gate should be a new line here, not a new
 * branch in the caller (Kraft-yytk).
 */
export const ARTIFACT_LABELS: Record<string, string> = {
  spec_approval: "Review spec",
  plan_approval: "Review plan",
  chain_finalized: "Review chain",
  human_review_approval: "Review brief",
};

/**
 * The node a rejection re-enters the chain at, when that is not the gate's own
 * node. Read from the item's chain rather than hard-coded per gate: the
 * routing is chain shape, and the server resolves it from the same field.
 */
const rejectTarget = (item: WorkItem, gate: string): string | null => {
  const nodes = item.chain_definition?.nodes ?? [];
  const at = nodes.findIndex((n) => n.gate_after === gate);
  if (at < 0) return null;
  const to = nodes[at].reject_to;
  return to && nodes.slice(0, at).some((n) => n.id === to) ? to : null;
};

/**
 * Whether the "tests passed" a human is about to trust was measured on the
 * commit in front of them (Kraft-lu2). No session, or one with no `head_sha`
 * stamped (a builtin, or a session older than the column), renders nothing —
 * a gate that cannot answer the question should not manufacture doubt.
 */
function testEvidence(item: WorkItem, sessions?: WorkerSession[]): ReactNode {
  const runs = (sessions ?? []).filter((s) => s.hook_point === "on.test.run");
  const last = runs.at(-1);
  if (!last || !last.head_sha) return null;
  const short = last.head_sha.slice(0, 7);
  if (last.head_sha === item.head_sha) {
    return <p className="field-hint">tests passed on {short}</p>;
  }
  return (
    <p className="field-hint gate-stale">
      stale: tests last ran on {short}
    </p>
  );
}

export function Gate({
  item,
  gate,
  variant = "card",
  sub,
  artifact,
  deferred,
  concerns,
  sessions,
}: {
  item: WorkItem;
  gate: string;
  variant?: "card" | "inline";
  /** Meta line under the title — which hook finished, how many reject loops are left. */
  sub?: ReactNode;
  /** The one linked artifact the decision is about (the plan, the spec, the MR). */
  artifact?: ReactNode;
  /** Findings that never entered the fix loop — the human triages them here. */
  deferred?: Finding[];
  /** `done_with_concerns` text from sessions along the way — same shape of
   *  thing as `deferred`, so it shares the one panel rather than a second. */
  concerns?: string[];
  /** Passed only from the detail view; lets `Escalate` show a running
   *  escalation turn instead of re-offering the button. The board's inline
   *  row has no per-item session list, so it goes without. */
  sessions?: WorkerSession[];
}) {
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const target = rejectTarget(item, gate);

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const approve = (
    <button
      className="btn btn-primary"
      disabled={busy}
      onClick={() => act(() => api.approveGate(item.id, gate))}
    >
      {variant === "card" && <Check size={14} />}
      Approve
    </button>
  );
  const startReject = (
    <button className="btn btn-secondary" disabled={busy} onClick={() => setRejecting(true)}>
      Reject…
    </button>
  );
  const rejectForm = (
    <div className="gate-reject">
      <textarea
        className="input"
        aria-label="reject note"
        placeholder="What should change?"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      {target && (
        <p className="field-hint">
          re-enters at <code>{target}</code> with this note as its steer
        </p>
      )}
      <div className="gate-actions">
        <button
          className="btn btn-primary"
          disabled={busy || note.trim() === ""}
          onClick={() => act(() => api.rejectGate(item.id, gate, note))}
        >
          {target ? "Reject and send back" : "Reject and re-plan"}
        </button>
        <button className="btn btn-ghost" disabled={busy} onClick={() => setRejecting(false)}>
          Cancel
        </button>
      </div>
    </div>
  );

  if (variant === "inline") {
    // The board's inline row has nowhere to show the deferred-minor-findings
    // roll-up (detail-only, see WorkItem.deferred_findings) — approving here
    // would be the blind approval spec §2 calls a silent discard. Route
    // through the detail view instead of rendering Approve at all, rather
    // than fetching a count for every gated row just to badge it.
    if (gate === "human_review_approval") {
      return (
        <div className="gate-inline" data-gate={gate}>
          <span className="gate-name" title={gate}>
            <Flag size={13} />
            approve the merge request
          </span>
          <Link className="btn btn-secondary" to={`/work-items/${item.id}`}>
            Review to approve
          </Link>
          <Escalate item={item} sessions={sessions} />
        </div>
      );
    }
    return (
      <div className="gate-inline" data-gate={gate}>
        {!rejecting ? (
          <>
            {/* The board has room for a phrase, not a paragraph: the same
                prompt without its trailing clause. One source, not a second
                map of labels to keep in step. */}
            <span className="gate-name" title={gate}>
              <Flag size={13} />
              {(PROMPTS[gate] ?? "approve to continue").replace(/ to continue$/, "")}
            </span>
            {approve}
            {startReject}
            <Escalate item={item} sessions={sessions} />
          </>
        ) : (
          rejectForm
        )}
        {err && <p className="form-error">{err}</p>}
      </div>
    );
  }

  return (
    <div className="card attention-card" data-gate={gate}>
      <div className="attention-head">
        <Flag size={18} className="attention-glyph" />
        <div className="attention-text">
          {/* The sentence is what the person acts on; the gate's own name is
              the identifier behind it, so it follows in the meta line rather
              than leading the headline. */}
          <span className="attention-title">{PROMPTS[gate] ?? "approve to continue"}</span>
          <span className="attention-sub">
            <code>{gate}</code>
            {sub != null && <> · {sub}</>}
          </span>
        </div>
      </div>
      {artifact}
      {testEvidence(item, sessions)}
      {((deferred && deferred.length > 0) || (concerns && concerns.length > 0)) && (
        <ul className="gate-deferred">
          {concerns?.map((c, i) => (
            <li key={`concern:${i}`}>
              <span className="field-hint">concern</span> {c}
            </li>
          ))}
          {deferred?.map((f) => (
            <li key={`${f.source_plugin}:${f.file}:${f.message}`}>
              <span className="field-hint">{f.severity}</span>{" "}
              <span className="mono">{f.file ? `${f.file}:${f.line ?? "?"}` : "—"}</span>{" "}
              {f.message}
            </li>
          ))}
        </ul>
      )}
      {!rejecting ? (
        <div className="gate-actions">
          {approve}
          {startReject}
          <Escalate item={item} sessions={sessions} />
        </div>
      ) : (
        rejectForm
      )}
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}
