import { useState } from "react";
import { ArrowSquareOut, Check, Flag } from "@phosphor-icons/react";
import * as api from "../../../api";
import { elapsedBetween, waitingSince } from "../../../format";
import type { KraftEvent, WorkItem } from "../../../types";
import type { InspectorTab } from "../selection";
import { Composer } from "./Composer";
import { useActionBar } from "./useActionBar";
import { SkipControl } from "../../../components/SkipControl";

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

/** "3 findings deferred · 1 concern" -- counted, never the messages
 *  themselves (Kraft-a4js: those are one click away, on the Timeline). */
function deferredSummary(item: WorkItem): string {
  const n = item.deferred_findings?.length ?? 0;
  const c = item.concerns?.length ?? 0;
  const parts: string[] = [];
  if (n > 0) parts.push(`${n} finding${n === 1 ? "" : "s"} deferred`);
  if (c > 0) parts.push(`${c} concern${c === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

/** The node whose `gate_after` is this gate — where "Review spec"/"Review
 *  changes" should land, which is not necessarily the item's *current* node
 *  (a later node may already be running while this gate waits). */
function gateNodeId(item: WorkItem, gate: string): string | null {
  const nodes = item.chain_definition?.nodes ?? [];
  return nodes.find((n) => n.gate_after === gate)?.id ?? null;
}

/** The node whose `findings_measured` event the deferred-findings count came
 *  from — for `human_review_approval` that's `verify`/`mr_checks`, never the
 *  `human_review` gate node itself, which emits no such event. The default
 *  chain measures at both `verify` and `mr_checks`, so the newest such event
 *  is not necessarily the one that actually carries a finding (`mr_checks` is
 *  commonly empty) -- skip empty ones so the link lands where the count came
 *  from. Falls back to `fallback` (the gate node) so the link still goes
 *  somewhere on an item with a nonzero count but no matching event yet. */
function findingsNodeId(events: KraftEvent[], fallback: string): string {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.type !== "findings_measured") continue;
    const payload = e.payload as Record<string, unknown>;
    const findings = payload.findings;
    if (!Array.isArray(findings) || findings.length === 0) continue;
    const id = payload.node_id;
    if (typeof id === "string") return id;
  }
  return fallback;
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
  reviewHref,
  events = [],
}: {
  item: WorkItem;
  gate: string;
  open: boolean;
  onOpen: () => void;
  onCancel: () => void;
  reviewHref: (nodeId: string, tab: InspectorTab, id: string) => string;
  events?: KraftEvent[];
}) {
  const { busy, pending, err, run } = useActionBar(item.id);
  const [note, setNote] = useState("");
  const target = rejectTarget(item, gate);
  const node = gateNodeId(item, gate) ?? item.current_node_id ?? "";
  // Waiting on a person is its own clock, separate from the node's frozen
  // run time in the hero (W0.4).
  const since = waitingSince(events, gate);
  const judge = item.judge_stop_note ?? [];
  const deferred = (item.deferred_findings?.length ?? 0) > 0 || (item.concerns?.length ?? 0) > 0;

  // Always shown, not just when an artifact exists: an absent one is
  // rendered disabled with "not written yet" rather than hidden (G5-05
  // — a gap the punch list flagged as a loose sentence instead).
  const artifacts = (
    <div className="gate-artifacts">
      {gate !== "human_review_approval" &&
        (item.gate_artifact ? (
          // No document id to hand `reviewHref` — `Documents.tsx` already
          // matches `item.gate_artifact` by repo path and selects it
          // itself once the list lands (Kraft-esc); don't rebuild that.
          <a className="btn btn-secondary" href={reviewHref(node, "documents", "")}>
            {ARTIFACT_LABELS[gate] ?? "Read document"}
          </a>
        ) : (
          <span className="btn btn-secondary" aria-disabled="true" title="not written yet">
            {ARTIFACT_LABELS[gate] ?? "Read document"}
          </span>
        ))}
      {gate === "human_review_approval" && (
        <a className="btn btn-secondary" href={reviewHref(node, "changes", "")}>
          Review changes
        </a>
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
  );

  return (
    <div className="card attention-card gate-card" data-gate={gate} data-testid="gate-card">
      <div className="attention-head">
        <Flag size={18} className="attention-glyph" />
        <div className="attention-text">
          <span className="attention-title">{PROMPTS[gate] ?? "approve to continue"}</span>
          <span className="attention-sub">
            <code>{gate}</code>
            {since && <> · waiting {elapsedBetween(since)}</>}
          </span>
        </div>
      </div>
      {/* Findings and the judge note side by side above 1280, one column
          below (W0.3); both collapse to one line at any fit step. */}
      {(deferred || judge.length > 0) && (
        <div className="gate-notes">
          {/* Kraft-a4js: an unbounded list here (10 findings, 5.4k characters
              on the live item) pushed the graph/inspector/right pane off the
              viewport on a page that deliberately cannot scroll (screen 48).
              `findings_measured` events are already on the Timeline, which
              scrolls -- so the card stays a counted one-liner. */}
          {deferred && (
            <p className="gate-deferred">
              {deferredSummary(item)} ·{" "}
              <a href={reviewHref(node, "timeline", findingsNodeId(events, node))}>see Timeline</a>
            </p>
          )}
          {/* stop_downgrade findings: real (critical/important) findings a
              judge decided were not worth chasing further -- visually
              distinct from .gate-deferred so a human at the gate can tell
              "these were never blocking" from "a judge decided not to keep
              chasing these". Counted per node, same as above. */}
          {judge.length > 0 && (
            <div className="gate-judge-note">
              {judge.map((n, i) => (
                <p key={`${n.node_id}:${i}`} className="gate-judge-entry">
                  <span className="field-hint">judge stopped {n.node_id} early</span> {n.reasoning} ·{" "}
                  {n.findings.length} finding{n.findings.length === 1 ? "" : "s"} not chased ·{" "}
                  <a href={reviewHref(n.node_id, "timeline", n.node_id)}>see Timeline</a>
                </p>
              ))}
              {judge.length > 1 && <span className="gate-more">+{judge.length - 1} more</span>}
            </div>
          )}
        </div>
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
          {/* One row of actions (44): the review link sits beside Approve /
              Reject instead of a row of its own. */}
          {artifacts}
          <SkipControl itemId={item.id} />
          {/* W6.3: between the server saying yes and the store re-reading the
              item, the card says so instead of offering Approve again. */}
          {pending && <span className="field-hint action-pending">pending…</span>}
          {err && <p className="form-error">{err}</p>}
        </div>
      ) : (
        <>
          {artifacts}
          <Composer
            title={`Reject ${gate}`}
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
              ).then((ok) => {
                if (ok) {
                  setNote("");
                  onCancel();
                }
              })
            }
            onCancel={onCancel}
          />
        </>
      )}
    </div>
  );
}
