import { useState, type ReactNode } from "react";
import { Check, Flag } from "@phosphor-icons/react";
import * as api from "../api";
import type { WorkItem } from "../types";

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

/** `human_review_approval` has nowhere to loop back to, so rejecting it stops. */
const rejectLabel = (gate: string) =>
  gate === "human_review_approval" ? "Reject and stop" : "Reject and re-plan";

export function Gate({
  item,
  gate,
  variant = "card",
  sub,
  artifact,
}: {
  item: WorkItem;
  gate: string;
  variant?: "card" | "inline";
  /** Meta line under the title — which hook finished, how many reject loops are left. */
  sub?: ReactNode;
  /** The one linked artifact the decision is about (the plan, the spec, the MR). */
  artifact?: ReactNode;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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
      <div className="gate-actions">
        <button
          className="btn btn-primary"
          disabled={busy || note.trim() === ""}
          onClick={() => act(() => api.rejectGate(item.id, gate, note))}
        >
          {rejectLabel(gate)}
        </button>
        <button className="btn btn-ghost" disabled={busy} onClick={() => setRejecting(false)}>
          Cancel
        </button>
      </div>
    </div>
  );

  if (variant === "inline") {
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
      {!rejecting ? (
        <div className="gate-actions">
          {approve}
          {startReject}
        </div>
      ) : (
        rejectForm
      )}
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}
