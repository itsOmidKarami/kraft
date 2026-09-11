import { useState } from "react";
import { ArrowSquareOut, Check, Flag } from "@phosphor-icons/react";
import * as api from "../../../api";
import type { WorkItem } from "../../../types";
import { Composer } from "./Composer";
import { useActionBar } from "./useActionBar";

const PROMPTS: Record<string, string> = {
  spec_approval: "approve the spec to continue",
  plan_approval: "approve the plan to continue",
  chain_finalized: "approve the revised chain to continue",
  human_review_approval: "approve the merge request to continue",
};
const ARTIFACT_LABELS: Record<string, string> = {
  spec_approval: "Review spec",
  plan_approval: "Review plan",
  chain_finalized: "Review chain",
  human_review_approval: "Review brief",
};

export function rejectTarget(item: WorkItem, gate: string): string | null {
  const nodes = item.chain_definition?.nodes ?? [];
  const at = nodes.findIndex((n) => n.gate_after === gate);
  if (at < 0) return null;
  const to = nodes[at].reject_to;
  return to && nodes.slice(0, at).some((n) => n.id === to) ? to : null;
}

/** The gate card (screen 19, "under the header, above the bar"). Replaces
 *  `Gate.tsx` + `ArtifactModal` for the item page's own gate state; reuses
 *  `.attention-card`/`.attention-head`/`-title`/`-sub`/`.gate-actions` from
 *  `styles.css` -- still alive, `Gate.tsx` itself uses them (kept for the
 *  Board group's `PeekPane.tsx`). */
export function GateCard({
  item,
  gate,
  open,
  onOpen,
  onCancel,
  onReadDoc,
  onReviewChanges,
}: {
  item: WorkItem;
  gate: string;
  open: boolean;
  onOpen: () => void;
  onCancel: () => void;
  onReadDoc: () => void;
  onReviewChanges: () => void;
}) {
  const { busy, err, run } = useActionBar(item.id);
  const [note, setNote] = useState("");
  const target = rejectTarget(item, gate);

  return (
    <div className="card attention-card gate-card" data-gate={gate} data-testid="gate-card">
      <div className="attention-head">
        <Flag size={18} className="attention-glyph" />
        <div className="attention-text">
          <span className="attention-title">{PROMPTS[gate] ?? "approve to continue"}</span>
          <span className="attention-sub">
            <code>{gate}</code>
          </span>
        </div>
      </div>
      {(item.gate_artifact || gate === "human_review_approval") && (
        <div className="gate-artifacts">
          {item.gate_artifact && (
            <button className="btn btn-secondary" onClick={onReadDoc}>
              {ARTIFACT_LABELS[gate] ?? "Read document"}
            </button>
          )}
          {gate === "human_review_approval" && (
            <button className="btn btn-secondary" onClick={onReviewChanges}>
              Review changes
            </button>
          )}
          {item.mr_ref && (
            <a
              className="btn btn-secondary mr-btn"
              href={item.mr_ref.url}
              target="_blank"
              rel="noreferrer"
            >
              <ArrowSquareOut size={14} /> Open MR
            </a>
          )}
        </div>
      )}
      {/* Deferred minor findings and done_with_concerns notes, one panel --
          what the reviewer let through, shown where the merge is decided. */}
      {((item.deferred_findings?.length ?? 0) > 0 || (item.concerns?.length ?? 0) > 0) && (
        <ul className="gate-deferred">
          {item.concerns?.map((c, i) => (
            <li key={`concern:${i}`}>
              <span className="field-hint">concern</span> {c}
            </li>
          ))}
          {item.deferred_findings?.map((f) => (
            <li key={`${f.source_plugin}:${f.file}:${f.message}`}>
              <span className="field-hint">{f.severity}</span>{" "}
              <span className="mono">{f.file ? `${f.file}:${f.line ?? "?"}` : "—"}</span>{" "}
              {f.message}
            </li>
          ))}
        </ul>
      )}
      {!open ? (
        <div className="gate-actions">
          <button
            className="btn btn-primary"
            disabled={busy}
            onClick={() =>
              run(() => api.approveGate(item.id, gate), "Approved — chain continues")
            }
          >
            <Check size={14} /> Approve
          </button>
          <button className="btn btn-secondary" disabled={busy} onClick={onOpen}>
            Reject
          </button>
          {err && <p className="form-error">{err}</p>}
        </div>
      ) : (
        <Composer
          value={note}
          onChange={setNote}
          busy={busy}
          error={err}
          placeholder="What should change?"
          footnote={
            target ? (
              <>
                re-enters at <code>{target}</code> with this note as its steer
              </>
            ) : undefined
          }
          submitLabel={target ? "Reject and send back" : "Reject and re-plan"}
          disabled={note.trim() === ""}
          onSubmit={() =>
            run(
              () => api.rejectGate(item.id, gate, note),
              `Rejected — re-running from ${target ?? gate}`,
            )
          }
          onCancel={onCancel}
        />
      )}
    </div>
  );
}
