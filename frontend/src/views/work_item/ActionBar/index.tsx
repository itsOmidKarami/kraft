import { useEffect, useState } from "react";
import { ArrowSquareOut, FolderOpen, Pause, Play } from "@phosphor-icons/react";
import * as api from "../../../api";
import { OverflowMenu } from "../../../components/ui";
import { deriveState } from "../../../deriveState";
import { tokens, usd, until } from "../../../format";
import type { KraftEvent, WorkerSession, WorkItem } from "../../../types";
import { BudgetComposer } from "./BudgetComposer";
import { Composer } from "./Composer";
import {
  EscalatedCard,
  EscalatingPill,
  dismissedTurnId,
} from "./EscalationCard";
import { GateCard, rejectTarget } from "./GateCard";
import { PhoneComposer } from "../PhoneComposer";
import { RateLimitRow } from "./RateLimitRow";
import { useActionBar } from "./useActionBar";
import { usePhone } from "../usePhone";

/**
 * The action bar (UI v2 · 06): exactly the README's "Action vocabulary per
 * state" buttons on the left, composers expanding inside the bar, the
 * right side (MR link, Worktree, usage line, Cancel overflow) carried over
 * unchanged from the pre-06 `ActionBar.tsx`.
 */

type ComposerKind =
  "steer" | "steerRetry" | "reject" | "answer" | "escalate" | "budget";

const COMPOSER_TITLES: Record<ComposerKind, string> = {
  steer: "Steer",
  steerRetry: "Steer & retry",
  reject: "Reject",
  answer: "Answer",
  escalate: "Escalate",
  budget: "Raise budget",
};

/** Tasks/tokens are the detail, dropped first under 1279 (50); the `$`
 *  figure is what a glance at a narrow bar still needs, so it stays. */
function usageParts(item: WorkItem, taskCount: number): { detail: string; figure: string; full: string } {
  const detailParts = [`${taskCount} task${taskCount === 1 ? "" : "s"}`];
  const u = item.usage;
  let figure = "";
  if (u) {
    const node = u.by_node.find((n) => n.node === item.current_node_id);
    const sum = (r?: { tokens_in: number; tokens_out: number }) =>
      r ? r.tokens_in + r.tokens_out : 0;
    if (node) detailParts.push(`${tokens(sum(node))} tokens this node`);
    detailParts.push(`${tokens(sum(u.total))} total`);
    if (u.total.cost_usd > 0) figure = usd(u.total.cost_usd, u.total.cost_complete);
  }
  const full = figure ? [...detailParts, figure].join(" · ") : detailParts.join(" · ");
  return { detail: detailParts.join(" · "), figure, full };
}

function onServerMachine(): boolean {
  return ["localhost", "127.0.0.1", "[::1]", "::1"].includes(
    window.location.hostname,
  );
}

export function ActionBar({
  item,
  sessions,
  events,
  onReviewChanges,
  onReadDoc,
  onEditChain,
}: {
  item: WorkItem;
  sessions: WorkerSession[];
  events: KraftEvent[];
  onReviewChanges: () => void;
  onReadDoc: () => void;
  onEditChain: () => void;
}) {
  const { state: rawState } = deriveState(item, sessions, events);
  const escalationTurns = sessions
    .filter((s) => s.hook_point === "escalation")
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
  const latestTurn = escalationTurns.at(-1);
  // Dismiss is a localStorage write, not a store field the item's own
  // re-renders react to -- track it in state too so clicking Dismiss takes
  // the card off screen immediately instead of waiting on some unrelated
  // update to re-render this component.
  const [dismissed, setDismissed] = useState(() => dismissedTurnId(item.id));
  // A dismissed escalated turn is inert client-side state, not a server
  // fact (no field carries "dismissed") -- re-derive without any
  // escalation sessions, falling back to whatever underlying reason
  // (gate/capped/budget/question) triggered the stop.
  const state =
    rawState === "escalated" && latestTurn && dismissed === latestTurn.id
      ? deriveState(item, [], events).state
      : rawState;
  const [open, setOpen] = useState<ComposerKind | null>(null);
  const [text, setText] = useState("");
  const { busy, err, run } = useActionBar(item.id);
  const [cancelling, setCancelling] = useState(false);
  // Worktree/Cancel errors: both live on the right side, whose actions
  // no state's left-side hint reports.
  const [sideErr, setSideErr] = useState<string | null>(null);
  const local = onServerMachine();
  const phone = usePhone();

  // A composer left open across a state change (e.g. a gate got approved
  // from another tab) would submit against a state it no longer applies to.
  useEffect(() => {
    setOpen(null);
    setText("");
  }, [item.id, state]);

  const nodeSessions = sessions.filter(
    (s) => s.node_id === item.current_node_id,
  );
  const usage = usageParts(item, nodeSessions.length);
  const gate = item.pending_gate;

  const cancel = async () => {
    setCancelling(true);
    setSideErr(null);
    try {
      await api.abandonWorkItem(item.id);
    } catch (e) {
      setSideErr(e instanceof Error ? e.message : String(e));
    } finally {
      setCancelling(false);
    }
  };

  let left: React.ReactNode;
  let secondRow: React.ReactNode = null;

  switch (state) {
    case "running": {
      left = (
        <div className="control-row">
          <button
            className="btn btn-secondary"
            disabled={item.status !== "active"}
            onClick={() => run(() => api.pauseWorkItem(item.id), "Paused")}
          >
            <Pause size={14} />
            Pause
          </button>
          <button className="btn btn-ghost" disabled>
            Steer
          </button>
          <span className="control-hint">{err ?? "steer unlocks once paused"}</span>
        </div>
      );
      break;
    }
    case "rate_limited":
    case "waiting": {
      left = (
        <div className="control-row">
          <button
            className="btn btn-secondary"
            disabled={state === "rate_limited"}
            onClick={() => run(() => api.pauseWorkItem(item.id), "Paused")}
          >
            <Pause size={14} />
            Pause
          </button>
          <button className="btn btn-ghost" disabled>
            Steer
          </button>
          <button className="btn btn-ghost" disabled>
            Escalate…
          </button>
          <span className="control-hint">
            {err ??
              (state === "rate_limited"
                ? "not waiting on you"
                : `not waiting on you — next check ${until(item.retry_at)}`)}
          </span>
        </div>
      );
      secondRow =
        state === "rate_limited" ? <RateLimitRow item={item} /> : null;
      break;
    }
    case "not_started": {
      const nodes = item.chain_definition.nodes;
      const firstGate = nodes.find((n) => n.gate_after)?.gate_after ?? "none";
      left = (
        <div className="control-row">
          <button
            className="btn btn-primary"
            disabled={busy}
            onClick={() => run(() => api.resumeWorkItem(item.id), "Started")}
          >
            <Play size={14} />
            Start
          </button>
          <button className="btn btn-ghost" onClick={onEditChain}>
            Edit chain
          </button>
          <span className="control-hint">
            {err ??
              `created paused · nothing spends tokens until you start it · first gate ${firstGate}`}
          </span>
        </div>
      );
      break;
    }
    case "done": {
      left = (
        <div className="control-row">
          <button
            className="btn btn-secondary"
            onClick={() => run(() => api.archiveWorkItem(item.id), "Archived")}
          >
            Archive
          </button>
          <span className="control-hint">
            {err ?? (item.mr_ref ? `merged !${item.mr_ref.number}` : "")}
          </span>
        </div>
      );
      break;
    }
    case "abandoned": {
      // No Reopen -- no backend route exists (06: "otherwise it stays beaded").
      left = (
        <div className="control-row">
          <button
            className="btn btn-secondary"
            onClick={() => run(() => api.archiveWorkItem(item.id), "Archived")}
          >
            Archive
          </button>
          {err && <span className="control-hint">{err}</span>}
        </div>
      );
      break;
    }
    case "archived": {
      left = (
        <div className="control-row">
          <button
            className="btn btn-secondary"
            onClick={() => run(() => api.restoreWorkItem(item.id), "Restored")}
          >
            Restore
          </button>
          <span className="control-hint">
            {err ?? "read-only · worktree reclaimed"}
          </span>
        </div>
      );
      break;
    }
    case "paused": {
      if (open === "steer") {
        left = (
          <Composer
            title={`Steer ${item.current_node_id}`}
            explanation="the note leads attempt 2's system prompt"
            value={text}
            onChange={setText}
            busy={busy}
            error={err}
            placeholder="What should the next attempt know?"
            footnote="attempt 2 restarts the node's tasks · fix-loop progress is kept"
            submitLabel="Resume with this steer"
            onSubmit={() =>
              run(
                () => api.resumeWorkItem(item.id, text.trim()),
                "Steer applied — resumed",
              )
            }
            onCancel={() => setOpen(null)}
          />
        );
        break;
      }
      left = (
        <div className="control-row">
          <button
            className="btn btn-primary"
            disabled={busy}
            onClick={() => run(() => api.resumeWorkItem(item.id), "Resumed")}
          >
            Resume
          </button>
          {item.steerable !== false && (
            <button
              className="btn btn-secondary"
              onClick={() => setOpen("steer")}
            >
              Steer
            </button>
          )}
          {/* No Escalate here: escalate_work_item 409s on anything but
              needs_human, and a paused item isn't (Kraft-k5ol). */}
          <span className="control-hint">{err ?? ""}</span>
        </div>
      );
      break;
    }
    case "gate": {
      // Approve/Reject live on the GateCard (rendered above the bar,
      // screen 19); the bar itself only carries the shared Escalate…
      // button, same as every other state.
      left = (
        <div className="control-row">
          <button
            className="btn btn-secondary"
            onClick={() => setOpen("escalate")}
          >
            Escalate…
          </button>
          <span className="control-hint">
            {gate} · reject walks back to{" "}
            {rejectTarget(item, gate ?? "") ?? "re-runs this node"}
          </span>
        </div>
      );
      break;
    }
    case "capped": {
      const node = item.chain_definition.nodes.find(
        (n) => n.id === item.current_node_id,
      );
      if (open === "steerRetry") {
        left = (
          <Composer
            title={`Steer & retry ${item.current_node_id}`}
            explanation="the note carries into the retry's system prompt"
            value={text}
            onChange={setText}
            busy={busy}
            error={err}
            placeholder="What should change before the retry?"
            footnote="retry resets the loop counter; steer text carries into cycle 1"
            submitLabel="Steer & retry"
            onSubmit={() =>
              run(
                () => api.retryWorkItem(item.id, text.trim() || undefined),
                "Steer applied — retrying",
              )
            }
            onCancel={() => setOpen(null)}
          />
        );
        break;
      }
      left = (
        <div className="control-row">
          {item.steerable === false ? (
            <button
              className="btn btn-primary"
              disabled={busy}
              onClick={() => run(() => api.retryWorkItem(item.id), "Retried")}
            >
              Steer & retry
            </button>
          ) : (
            <button
              className="btn btn-primary"
              onClick={() => setOpen("steerRetry")}
            >
              Steer & retry
            </button>
          )}
          <button
            className="btn btn-secondary"
            onClick={() => setOpen("escalate")}
          >
            Escalate
          </button>
          <span className="control-hint">
            {err ??
              (item.cappedOut
                ? `${node?.fix_loop ?? item.current_node_id} hit its cap · ${item.cappedOut.attempts} attempts`
                : (item.stop_reason ?? "stopped without finishing · retry picks up where it left off"))}
          </span>
        </div>
      );
      break;
    }
    case "budget": {
      if (open === "steerRetry") {
        left = (
          <Composer
            title={`Steer & retry ${item.current_node_id}`}
            explanation="the note carries into the retry's system prompt"
            value={text}
            onChange={setText}
            busy={busy}
            error={err}
            placeholder="What should change before the retry?"
            footnote="retry resets the loop counter; steer text carries into cycle 1"
            submitLabel="Steer & retry"
            onSubmit={() =>
              run(
                () => api.retryWorkItem(item.id, text.trim() || undefined),
                "Steer applied — retrying",
              )
            }
            onCancel={() => setOpen(null)}
          />
        );
        break;
      }
      if (open === "budget") {
        const capUsd = item.budget?.cap_usd ?? 0;
        left = (
          <BudgetComposer
            itemId={item.id}
            capUsd={capUsd}
            spentUsd={item.budget?.spent_usd}
            busy={busy}
            err={err}
            run={run}
            onCancel={() => setOpen(null)}
          />
        );
        break;
      }
      left = (
        <div className="control-row">
          <button className="btn btn-primary" onClick={() => setOpen("budget")}>
            Raise budget…
          </button>
          <button
            className="btn btn-secondary"
            onClick={() => setOpen("steerRetry")}
          >
            Steer
          </button>
          <button
            className="btn btn-secondary"
            onClick={() => setOpen("escalate")}
          >
            Escalate…
          </button>
          <span className="control-hint">
            {err ?? `cap $${item.budget?.cap_usd} refused the next task`}
          </span>
        </div>
      );
      break;
    }
    case "question": {
      if (open === "answer") {
        left = (
          <Composer
            title="The agent asks"
            footnote="the question and answer are kept in the timeline"
            value={text}
            onChange={setText}
            busy={busy}
            error={err}
            quoted={item.needs_context_question ?? undefined}
            submitLabel="Answer and resume"
            disabled={text.trim() === ""}
            onSubmit={() =>
              run(
                () => api.resumeWorkItem(item.id, text.trim()),
                "Answered — resumed",
              )
            }
            onCancel={() => setOpen(null)}
          />
        );
        break;
      }
      left = (
        <div className="control-row">
          <button className="btn btn-primary" onClick={() => setOpen("answer")}>
            Answer
          </button>
          <button
            className="btn btn-secondary"
            onClick={() => setOpen("escalate")}
          >
            Escalate…
          </button>
          <span className="control-hint">
            {err ?? "nothing runs until you answer"}
          </span>
        </div>
      );
      break;
    }
    case "escalating": {
      // The turn is live -- Steer & retry / Escalate are visibly disabled
      // ("one escalation turn at a time"), and the only live control is
      // Stop agent (06 "Stop agent").
      left = latestTurn ? (
        <EscalatingPill
          turn={latestTurn.attempt}
          busy={busy}
          onStop={() => run(() => api.stopEscalation(item.id), "Agent stopped")}
        />
      ) : null;
      break;
    }
    case "escalated": {
      // The card itself (Apply as steer & retry / Reply / Dismiss) renders
      // above the bar, same slot as GateCard; the bar's own left side stays
      // empty for this state.
      left = null;
      break;
    }
    default: {
      left = null;
    }
  }

  // The one composer every state's Escalate…/Reply opens (18: "the
  // message; when turn > 1 it shows the thread above"). Overrides
  // whatever the switch above set for `left` -- unambiguous, since `open`
  // only ever becomes "escalate" from a trigger that state itself renders.
  const priorTurn = escalationTurns.length > 0;
  if (open === "escalate") {
    left = (
      <>
        {priorTurn && (
          <div className="escalation-thread" data-testid="escalation-thread">
            {escalationTurns.map((s) => {
              const sent = events.find(
                (e) =>
                  e.type === "escalation_message" &&
                  e.payload.session_id === s.id,
              );
              return (
                <p key={s.id} className="field-hint">
                  turn {s.attempt}: {String(sent?.payload.message ?? "")}
                </p>
              );
            })}
          </div>
        )}
        <Composer
          title={`Escalation · turn ${escalationTurns.length + 1}`}
          value={text}
          onChange={setText}
          busy={busy}
          error={err}
          placeholder="What should the agent look at?"
          submitLabel="Send to agent"
          disabled={text.trim() === ""}
          onSubmit={() =>
            run(
              () => api.escalateWorkItem(item.id, text.trim()),
              "Sent to the agent",
            )
          }
          onCancel={() => setOpen(null)}
        />
      </>
    );
  }

  // m08: a composer is a full-screen page on phone, not an inline expand.
  // `reject` is excluded -- it lives inside `GateCard` (rendered separately,
  // above the bar), not in `left`.
  if (phone && open && open !== "reject") {
    left = (
      <PhoneComposer
        title={COMPOSER_TITLES[open]}
        context={`${item.id} · ${item.current_node_id ?? "—"}`}
        onCancel={() => setOpen(null)}
      >
        {left}
      </PhoneComposer>
    );
  }

  return (
    <>
      {state === "gate" && gate && (
        <GateCard
          item={item}
          gate={gate}
          open={open === "reject"}
          onOpen={() => setOpen("reject")}
          onCancel={() => setOpen(null)}
          onReadDoc={onReadDoc}
          onReviewChanges={onReviewChanges}
        />
      )}
      {state === "escalated" && latestTurn && (
        <EscalatedCard
          item={item}
          session={latestTurn}
          events={events}
          onOpenReply={() => setOpen("escalate")}
          onDismiss={() => setDismissed(latestTurn.id)}
        />
      )}
      <div className="action-bar card">
        <div className="action-bar-row">
          <div className="action-bar-left">{left}</div>

          <div className="action-bar-mid" />

          <div className="action-bar-right">
            {gate !== "human_review_approval" && (
              <button className="btn btn-secondary" onClick={onReviewChanges}>
                Review changes
              </button>
            )}
            {item.mr_ref ? (
              <a
                className="btn btn-secondary mr-btn"
                href={item.mr_ref.url}
                target="_blank"
                rel="noreferrer"
              >
                <ArrowSquareOut size={14} />
                MR !{item.mr_ref.number}
              </a>
            ) : (
              <button
                className="btn btn-ghost mr-btn"
                disabled
                title="opens once the MR node has run"
              >
                MR
                <ArrowSquareOut size={14} />
              </button>
            )}
            {local && (
              <button
                className="btn btn-ghost"
                onClick={() =>
                  api.openWorktree(item.id).then(
                    () => setSideErr(null),
                    (e) =>
                      setSideErr(
                        e instanceof Error ? e.message : String(e),
                      ),
                  )
                }
              >
                <FolderOpen size={14} />
                Worktree
              </button>
            )}
            <span className="action-bar-usage" title={usage.full}>
              <span className="control-usage-detail">{usage.detail}</span>
              {usage.figure && <> · {usage.figure}</>}
            </span>
            {item.status !== "completed" &&
              item.status !== "abandoned" &&
              item.status !== "active" && (
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
            {sideErr && <span className="control-hint">{sideErr}</span>}
          </div>
        </div>

        {secondRow}
      </div>
    </>
  );
}
