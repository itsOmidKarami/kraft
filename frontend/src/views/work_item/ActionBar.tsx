import { useState } from "react";
import { ArrowSquareOut, ChatText, FolderOpen, Pause } from "@phosphor-icons/react";
import * as api from "../../api";
import { ArtifactModal } from "../../components/ArtifactModal";
import { BudgetCard } from "../../components/BudgetCard";
import { CappedCard } from "../../components/CappedCard";
import { Escalate } from "../../components/Escalate";
import { ARTIFACT_LABELS, Gate } from "../../components/Gate";
import { OverflowMenu } from "../../components/ui";
import { PausedCard } from "../../components/PausedCard";
import { SkipControl } from "../../components/SkipControl";
import { tokens, usd } from "../../format";
import { useStore } from "../../store";
import type { KraftEvent, WorkerSession, WorkItem } from "../../types";

/**
 * The action bar container (UI v2 · 05, 11 right side + today's cards in
 * the left slot). The left slot — state buttons, composers, the gate card's
 * own action row — belongs to UI v2 · 06 (Item actions), which starts once
 * this merges; until then this renders today's `PausedCard` / `CappedCard` /
 * `BudgetCard` / `Gate` / `Escalate` / `NeedsContextCard` there unchanged, so
 * nothing regresses (group spec "Not yours").
 */

/**
 * The needs_context answer card — a `needs_human` stop whose reason is an
 * agent's question, answerable the same way a pause is.
 */
function NeedsContextCard({ item, sessions }: { item: WorkItem; sessions: WorkerSession[] }) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const hydrateItem = useStore((s) => s.hydrateItem);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.resumeWorkItem(item.id, answer.trim());
      hydrateItem(item.id).catch(() => {});
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card attention-card" data-testid="needs-context-card">
      <div className="attention-head">
        <ChatText size={18} className="attention-glyph" />
        <div className="attention-text">
          <span className="attention-title">{item.needs_context_question}</span>
          <span className="attention-sub">the agent stopped to ask this before continuing</span>
        </div>
      </div>
      <div className="field">
        <label htmlFor="needs-context-answer">
          Answer <span className="field-hint">· goes into the next attempt's system prompt</span>
        </label>
        <textarea
          id="needs-context-answer"
          className="input"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
        />
      </div>
      <div className="gate-actions capped-actions">
        <button className="btn btn-primary" disabled={busy || answer.trim() === ""} onClick={submit}>
          <ChatText size={14} />
          Answer
        </button>
        <Escalate item={item} sessions={sessions} />
      </div>
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}

function usageLine(item: WorkItem, taskCount: number): string {
  const parts = [`${taskCount} task${taskCount === 1 ? "" : "s"}`];
  const u = item.usage;
  if (u) {
    const node = u.by_node.find((n) => n.node === item.current_node_id);
    const sum = (r?: { tokens_in: number; tokens_out: number }) => (r ? r.tokens_in + r.tokens_out : 0);
    if (node) parts.push(`${tokens(sum(node))} tokens this node`);
    parts.push(`${tokens(sum(u.total))} total`);
    if (u.total.cost_usd > 0) parts.push(usd(u.total.cost_usd, u.total.cost_complete));
  }
  return parts.join(" · ");
}

function onServerMachine(): boolean {
  return ["localhost", "127.0.0.1", "[::1]", "::1"].includes(window.location.hostname);
}

export function ActionBar({
  item,
  sessions,
  events,
  onReviewChanges,
}: {
  item: WorkItem;
  sessions: WorkerSession[];
  events: KraftEvent[];
  onReviewChanges: () => void;
}) {
  const [pausing, setPausing] = useState(false);
  const [pauseErr, setPauseErr] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancelErr, setCancelErr] = useState<string | null>(null);
  const [showArtifact, setShowArtifact] = useState(false);
  const [worktreeErr, setWorktreeErr] = useState<string | null>(null);
  const local = onServerMachine();

  const gate = item.pending_gate ?? null;
  const nodeSessions = sessions.filter((s) => s.node_id === item.current_node_id);
  const stranded = !gate && item.status === "needs_human";

  const pause = async () => {
    setPausing(true);
    setPauseErr(null);
    try {
      await api.pauseWorkItem(item.id);
    } catch (e) {
      setPauseErr(e instanceof Error ? e.message : String(e));
      setPausing(false);
    }
  };

  const cancel = async () => {
    setCancelling(true);
    setCancelErr(null);
    try {
      await api.abandonWorkItem(item.id);
    } catch (e) {
      setCancelErr(e instanceof Error ? e.message : String(e));
    } finally {
      setCancelling(false);
    }
  };

  return (
    <div className="action-bar card">
      <div className="action-bar-left">
        {item.status === "needs_human" && item.needs_context_question && !gate ? (
          <NeedsContextCard item={item} sessions={sessions} />
        ) : item.budget ? (
          <BudgetCard item={item} sessions={sessions} />
        ) : item.cappedOut || stranded ? (
          <CappedCard item={item} sessions={sessions} events={events} />
        ) : item.status === "paused" ? (
          <PausedCard item={item} sessions={sessions} />
        ) : gate ? (
          <Gate
            item={item}
            gate={gate}
            sessions={sessions}
            sub={`${nodeSessions.map((s) => s.hook_point).join(", ")} completed clean`}
            artifact={
              (item.gate_artifact || gate === "human_review_approval") && (
                <div className="gate-artifacts">
                  {item.gate_artifact && (
                    <button className="btn btn-secondary" onClick={() => setShowArtifact(true)}>
                      {ARTIFACT_LABELS[gate] ?? "Review document"}
                    </button>
                  )}
                  {gate === "human_review_approval" && (
                    <button className="btn btn-secondary" onClick={onReviewChanges}>
                      Review changes
                    </button>
                  )}
                </div>
              )
            }
            deferred={gate === "human_review_approval" ? item.deferred_findings : undefined}
            concerns={item.concerns}
          />
        ) : (
          <div className="control-row">
            <button className="btn btn-secondary" disabled={pausing || item.status !== "active"} onClick={pause}>
              <Pause size={14} />
              {pausing ? "Pausing…" : "Pause"}
            </button>
            <button className="btn btn-ghost" disabled>
              <ChatText size={14} />
              Steer
            </button>
            <SkipControl itemId={item.id} disabled={item.status !== "active"} />
            <span className="control-hint">{pauseErr ?? "steer unlocks once paused"}</span>
          </div>
        )}
      </div>

      <div className="action-bar-mid">
        {item.status !== "completed" && item.status !== "abandoned" && (
          <span className="control-hint">
            {cancelErr ?? (item.status === "active" ? "pause it before abandoning" : "")}
          </span>
        )}
      </div>

      <div className="action-bar-right">
        {gate !== "human_review_approval" && (
          <button className="btn btn-secondary" onClick={onReviewChanges}>
            Review changes
          </button>
        )}
        {item.mr_ref ? (
          <a className="btn btn-secondary mr-btn" href={item.mr_ref.url} target="_blank" rel="noreferrer">
            <ArrowSquareOut size={14} />
            MR !{item.mr_ref.number}
          </a>
        ) : (
          <button className="btn btn-ghost mr-btn" disabled title="opens once the MR node has run">
            MR
            <ArrowSquareOut size={14} />
          </button>
        )}
        {local && (
          <button
            className="btn btn-ghost"
            onClick={() =>
              api.openWorktree(item.id).then(
                () => setWorktreeErr(null),
                (e) => setWorktreeErr(e instanceof Error ? e.message : String(e)),
              )
            }
          >
            <FolderOpen size={14} />
            Worktree
          </button>
        )}
        <span className="action-bar-usage">{usageLine(item, nodeSessions.length)}</span>
        {item.status !== "completed" && item.status !== "abandoned" && item.status !== "active" && (
          <OverflowMenu
            items={[
              {
                label: cancelling ? "Cancelling…" : "Cancel work item",
                danger: true,
                confirm:
                  "Abandon this work item? This reclaims its worktree and destroys any uncommitted work.",
                onSelect: cancel,
              },
            ]}
          />
        )}
        {worktreeErr && <span className="control-hint">{worktreeErr}</span>}
      </div>

      {showArtifact && <ArtifactModal workItemId={item.id} onClose={() => setShowArtifact(false)} />}
    </div>
  );
}
