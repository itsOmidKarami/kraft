import { Fragment, useEffect, useId, useRef, useState, type ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowSquareOut, Check, Pause, Play, Robot, SkipForward } from "@phosphor-icons/react";
import * as api from "../../../api";
import { itemMenuItems } from "../../../components/itemMenu";
import { OverflowMenu, StatusGlyph, type OverflowItem } from "../../../components/ui";
import { deriveState } from "../../../deriveState";
import { ago, elapsedBetween, judgeReasoning, tokens, until, usd, waitingSince } from "../../../format";
import type { KraftEvent, WorkerSession, WorkItem } from "../../../types";
import { PhoneComposer } from "../PhoneComposer";
import type { InspectorTab } from "../selection";
import { usePhone } from "../usePhone";
import { BudgetComposer } from "./BudgetComposer";
import { Composer } from "./Composer";
import { EscalatingPill, dismissTurn, dismissedTurnId, useEscalationReport } from "./EscalationCard";
import { RateLimitRow } from "./RateLimitRow";
import { useActionBar } from "./useActionBar";

/**
 * The item card (W11 · A, design 1e): one card under the header on every
 * state. Its header names what the item waits on, the stats sit top-right, the
 * button row holds the state's one or two actions, and More actions holds the
 * rest. It replaced the gate card + action bar pair. Composers open inline
 * under the button row, as a full-screen sheet on phone.
 */

const PROMPTS: Record<string, string> = {
  spec_approval: "approve the spec to continue",
  plan_approval: "approve the plan to continue",
  chain_finalized: "approve the revised chain to continue",
  human_review_approval: "approve the merge request to continue",
};
/** The gate document's link names what it is where the gate says so; every
 *  other gate reads "Read document" (W11 rule 6). */
const ARTIFACT_LABELS: Record<string, string> = {
  spec_approval: "Review spec",
  plan_approval: "Review plan",
  chain_finalized: "Review chain",
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

/** The node whose `gate_after` is this gate -- where the gate's document and
 *  its findings link should land, which is not necessarily the item's
 *  *current* node. */
function gateNodeId(item: WorkItem, gate: string): string | null {
  return item.chain_definition?.nodes.find((n) => n.gate_after === gate)?.id ?? null;
}

/** The node whose `findings_measured` event the deferred-findings count came
 *  from -- the newest one that actually carries a finding (`mr_checks` is
 *  commonly empty), else the gate node. */
function findingsNodeId(events: KraftEvent[], fallback: string): string {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.type !== "findings_measured") continue;
    const payload = e.payload as Record<string, unknown>;
    if (!Array.isArray(payload.findings) || payload.findings.length === 0) continue;
    if (typeof payload.node_id === "string") return payload.node_id;
  }
  return fallback;
}

export type ComposerKind = "steer" | "steerRetry" | "reject" | "answer" | "escalate" | "budget" | "skip";

const COMPOSER_TITLES: Record<ComposerKind, string> = {
  steer: "Steer",
  steerRetry: "Steer & retry",
  reject: "Reject",
  answer: "Answer",
  escalate: "Escalate",
  budget: "Raise budget",
  skip: "Skip",
};

/** `1 task · 95.4k tokens · $5.01`: the node's tasks, the item's tokens and
 *  spend. The node's own tokens are in the title. */
function stats(item: WorkItem, taskCount: number): { text: string; title: string } {
  const parts = [`${taskCount} task${taskCount === 1 ? "" : "s"}`];
  const u = item.usage;
  const sum = (r: { tokens_in: number; tokens_out: number }) => r.tokens_in + r.tokens_out;
  let nodeTokens = "";
  if (u) {
    parts.push(`${tokens(sum(u.total))} tokens`);
    if (u.total.cost_usd > 0) parts.push(usd(u.total.cost_usd, u.total.cost_complete));
    const node = u.by_node.find((n) => n.node === item.current_node_id);
    if (node) nodeTokens = `${tokens(sum(node))} tokens this node`;
  }
  const text = parts.join(" · ");
  return { text, title: nodeTokens ? `${text} · ${nodeTokens}` : text };
}

/** A stop that waits on a person says how long, apart from the node's frozen
 *  run time in the header (W0.4); a gate counts from its own request. */
const WAITS_ON_PERSON = ["capped", "budget", "question", "escalated"];

export function ItemCard({
  item,
  sessions,
  events,
  onReviewChanges,
  onEditChain,
  reviewHref,
  variant = "page",
  onOpenItem,
  initialOpen,
}: {
  item: WorkItem;
  sessions: WorkerSession[];
  events: KraftEvent[];
  onReviewChanges: () => void;
  onEditChain: () => void;
  reviewHref: (nodeId: string, tab: InspectorTab, id: string) => string;
  /** The board peek renders the same card narrow (W11 · I): no stats line, no
   *  document link (the gate document opens on the item page), and "Open
   *  item →" leading More actions. */
  variant?: "page" | "peek";
  onOpenItem?: () => void;
  /** A board row's button asked for this composer (W11 · B.3). */
  initialOpen?: ComposerKind;
}) {
  const { state: rawState, needsYou } = deriveState(item, sessions, events);
  const escalationTurns = sessions
    .filter((s) => s.hook_point === "escalation")
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
  const latestTurn = escalationTurns.at(-1);
  // Dismiss is a localStorage write, not a store field the item re-renders on:
  // tracked in state too so the card changes the moment it is clicked.
  const [dismissed, setDismissed] = useState(() => dismissedTurnId(item.id));
  // A dismissed escalated turn falls back to whatever stopped the item.
  const state =
    rawState === "escalated" && latestTurn && dismissed === latestTurn.id
      ? deriveState(item, [], events).state
      : rawState;
  const [open, setOpen] = useState<ComposerKind | null>(initialOpen ?? null);
  const [text, setText] = useState("");
  const { busy, pending, err, run } = useActionBar(item.id);
  const [cancelling, setCancelling] = useState(false);
  const [sideErr, setSideErr] = useState<string | null>(null);
  const phone = usePhone();
  const hintId = useId();
  const report = useEscalationReport(item, state === "escalated" ? latestTurn : undefined, events);

  // A composer left open across a state change (a gate approved from another
  // tab) would submit against a state it no longer applies to. Only a change:
  // the composer a board row opened the card with stays open.
  const stateKey = `${item.id}:${state}`;
  const lastKey = useRef(stateKey);
  useEffect(() => {
    if (lastKey.current === stateKey) return;
    lastKey.current = stateKey;
    setOpen(null);
    setText("");
  }, [stateKey]);

  const openComposer = (k: ComposerKind) => {
    setText("");
    setOpen(k);
  };
  const close = () => setOpen(null);
  // A composer closes once its action lands (W6.3), whatever state the server
  // reports next -- it never sits open holding a sent note.
  const submit = (fn: () => Promise<unknown>, toast?: string) =>
    run(fn, toast).then((ok) => {
      if (ok) {
        setOpen(null);
        setText("");
      }
    });

  const cancelItem = async () => {
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

  const node = item.current_node_id;
  const nodeDef = item.chain_definition.nodes.find((n) => n.id === node);
  const gate = item.pending_gate ?? "";
  const waitingOn = WAITS_ON_PERSON.includes(state) ? waitingSince(events) : null;
  const waited = waitingOn ? `waiting ${elapsedBetween(waitingOn)}` : null;
  const nodeCode = node ? <code>{node}</code> : null;
  const stat = stats(item, sessions.filter((s) => s.node_id === node).length);
  const escalate: OverflowItem = {
    label: "Escalate",
    icon: <Robot size={14} />,
    hint: "ask an agent to decide",
    onSelect: () => openComposer("escalate"),
  };

  let title: ReactNode = null;
  const sub: ReactNode[] = [];
  let body: ReactNode = null;
  const row: ReactNode[] = [];
  const stateItems: OverflowItem[] = [];

  switch (state) {
    case "gate": {
      const since = waitingSince(events, gate);
      const gateNode = gateNodeId(item, gate) ?? node ?? "";
      const target = rejectTarget(item, gate);
      const deferred = deferredSummary(item);
      const rejectHint = target ? `reject walks back to ${target}` : "reject re-runs this node";
      title = PROMPTS[gate] ?? "approve to continue";
      sub.push(<code>{gate}</code>, since && `waiting ${elapsedBetween(since)}`);
      // The peek's header is `gate · waiting` only (I.1).
      if (deferred && variant === "page") {
        sub.push(
          <span className="item-card-deferred">{deferred}</span>,
          <a href={reviewHref(gateNode, "timeline", findingsNodeId(events, gateNode))}>see Timeline</a>,
        );
      }
      // The judge's stop note stays in the card (rule 9); it collapses to one
      // line at any fit step.
      const judge = item.judge_stop_note ?? [];
      if (judge.length > 0) {
        body = (
          <div className="gate-notes">
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
          </div>
        );
      }
      row.push(
        <button
          key="approve"
          className="btn btn-primary"
          disabled={busy}
          onClick={() => run(() => api.approveGate(item.id, gate), "Approved — chain continues")}
        >
          <Check size={14} /> Approve
        </button>,
        <button
          key="reject"
          className="btn btn-secondary"
          disabled={busy}
          title={rejectHint}
          aria-describedby={`${hintId}-reject`}
          onClick={() => openComposer("reject")}
        >
          Reject
        </button>,
        <span key="reject-hint" id={`${hintId}-reject`} hidden>
          {rejectHint}
        </span>,
      );
      // Always shown for a document gate: an absent document is a disabled
      // label saying so, not a missing link (G5-05). No document id to hand
      // `reviewHref` -- Documents.tsx selects the gate's own by path.
      if (gate !== "human_review_approval" && variant === "page") {
        const label = ARTIFACT_LABELS[gate] ?? "Read document";
        row.push(
          item.gate_artifact ? (
            <a key="doc" className="item-card-link" href={reviewHref(gateNode, "documents", "")}>
              {label}
            </a>
          ) : (
            <span key="doc" className="item-card-link" aria-disabled="true" title="not written yet">
              {label}
            </span>
          ),
        );
      }
      stateItems.push(
        {
          label: "Skip",
          icon: <SkipForward size={14} />,
          hint: "continue without approving",
          onSelect: () => openComposer("skip"),
        },
        escalate,
      );
      break;
    }
    case "running":
    case "rate_limited":
    case "waiting": {
      title =
        state === "running"
          ? "not waiting on you"
          : state === "rate_limited"
            ? "rate limited · not waiting on you"
            : "waiting on CI · not waiting on you";
      sub.push(nodeCode, state === "waiting" && item.retry_at && `next check ${until(item.retry_at)}`);
      row.push(
        <button
          key="pause"
          className="btn btn-primary"
          disabled={busy || (state === "running" ? item.status !== "active" : state === "rate_limited")}
          onClick={() => run(() => api.pauseWorkItem(item.id), "Paused")}
        >
          <Pause size={14} /> Pause
        </button>,
      );
      stateItems.push({ label: "Steer", disabled: true, hint: "steer unlocks once paused", onSelect: () => {} });
      break;
    }
    case "paused": {
      title = node ? `paused at ${node}` : "paused";
      row.push(
        <button
          key="resume"
          className="btn btn-primary"
          disabled={busy}
          onClick={() => run(() => api.resumeWorkItem(item.id), "Resumed")}
        >
          Resume
        </button>,
      );
      // No Escalate: escalate_work_item 409s on anything but needs_human (Kraft-k5ol).
      if (item.steerable !== false) {
        row.push(
          <button key="steer" className="btn btn-secondary" onClick={() => openComposer("steer")}>
            Steer
          </button>,
        );
      }
      break;
    }
    case "capped": {
      const judged = judgeReasoning(item.stop_reason);
      title = item.cappedOut
        ? `${nodeDef?.fix_loop ?? node} hit its cap`
        : judged !== undefined
          ? "stopped early (judge)"
          : "stopped without finishing";
      sub.push(
        nodeCode,
        waited,
        item.cappedOut
          ? `${item.cappedOut.attempts} attempts`
          : (judged ?? item.stop_reason ?? "retry picks up where it left off"),
      );
      row.push(
        item.steerable === false ? (
          <button
            key="retry"
            className="btn btn-primary"
            disabled={busy}
            onClick={() => run(() => api.retryWorkItem(item.id), "Retried")}
          >
            Steer & retry
          </button>
        ) : (
          <button key="retry" className="btn btn-primary" onClick={() => openComposer("steerRetry")}>
            Steer & retry
          </button>
        ),
        <button key="escalate" className="btn btn-secondary" onClick={() => openComposer("escalate")}>
          Escalate
        </button>,
      );
      break;
    }
    case "budget": {
      title = "spend cap reached";
      sub.push(nodeCode, waited, item.budget && `cap $${item.budget.cap_usd} refused the next task`);
      row.push(
        <button key="raise" className="btn btn-primary" onClick={() => openComposer("budget")}>
          Raise budget
        </button>,
        <button key="escalate" className="btn btn-secondary" onClick={() => openComposer("escalate")}>
          Escalate
        </button>,
      );
      stateItems.push({ label: "Steer & retry", onSelect: () => openComposer("steerRetry") });
      break;
    }
    case "question": {
      title = "the agent asks";
      sub.push(nodeCode, waited);
      // W8.1: the question itself, in full, above Answer.
      if (item.needs_context_question && open !== "answer") {
        body = (
          <p className="stop-question" data-testid="stop-question">
            {item.needs_context_question}
          </p>
        );
      }
      row.push(
        <button key="answer" className="btn btn-primary" onClick={() => openComposer("answer")}>
          Answer
        </button>,
      );
      stateItems.push(escalate);
      break;
    }
    case "escalating": {
      // `auto` tells an unattended fire (Kraft-vyk8) from a person's Escalate.
      const auto = Boolean(
        latestTurn &&
          events.find((e) => e.type === "escalation_message" && e.payload.session_id === latestTurn.id)?.payload.auto,
      );
      title = "escalation running";
      sub.push(nodeCode, auto ? "fired automatically — nobody had acted on it yet" : "one escalation turn at a time");
      if (latestTurn) row.push(<EscalatingPill key="pill" turn={latestTurn.attempt} auto={auto} />);
      stateItems.push({
        label: "Stop escalation",
        onSelect: () => run(() => api.stopEscalation(item.id), "Agent stopped"),
      });
      break;
    }
    case "escalated": {
      title = `Escalation · turn ${latestTurn?.attempt ?? 1} · reported`;
      sub.push(nodeCode, waited);
      // Markdown emits blocks, so a <div>; capped with its own scroll so the
      // buttons below stay on the never-scrolling page. While Reply is open the
      // thread above its composer carries every turn, so the body steps aside.
      if (open !== "escalate") body = (
        <div className="attention-sub doc-modal-body attention-sub-md">
          <Markdown remarkPlugins={[remarkGfm]}>{report.text}</Markdown>
        </div>
      );
      row.push(
        <button key="reply" className="btn btn-primary" disabled={busy} onClick={() => openComposer("escalate")}>
          Reply
        </button>,
        <button
          key="dismiss"
          className="btn btn-secondary"
          disabled={busy}
          onClick={() => {
            if (!latestTurn) return;
            dismissTurn(item.id, latestTurn.id);
            setDismissed(latestTurn.id);
          }}
        >
          Dismiss
        </button>,
      );
      // Only the agent's own summary is safe to feed back in as a steer.
      if (report.summary) {
        const summary = report.summary;
        stateItems.push({
          label: "Apply as steer & retry",
          onSelect: () => run(() => api.retryWorkItem(item.id, `From escalation: ${summary}`), "Applied as steer — retrying"),
        });
      }
      break;
    }
    case "done":
    case "archived": {
      const completed = [...events].reverse().find((e) => e.type === "work_item_completed");
      title = state === "done" ? "completed" : "archived · read-only";
      sub.push(
        item.mr_ref && `merged !${item.mr_ref.number}`,
        state === "done" ? ago(completed?.created_at ?? item.updated_at) : "worktree reclaimed",
      );
      row.push(
        item.mr_ref ? (
          <a key="mr" className="btn btn-primary" href={item.mr_ref.url} target="_blank" rel="noreferrer">
            Open MR <ArrowSquareOut size={14} />
          </a>
        ) : (
          <Fragment key="mr">
            <button className="btn btn-primary" disabled title="not opened yet" aria-describedby={`${hintId}-mr`}>
              Open MR <ArrowSquareOut size={14} />
            </button>
            <span id={`${hintId}-mr`} hidden>
              not opened yet
            </span>
          </Fragment>
        ),
      );
      break;
    }
    case "abandoned": {
      // No Reopen -- no backend route exists (06).
      title = "abandoned";
      sub.push(nodeCode, ago([...events].reverse().find((e) => e.type === "work_item_abandoned")?.created_at ?? item.updated_at));
      row.push(
        <button
          key="archive"
          className="btn btn-secondary"
          onClick={() => run(() => api.archiveWorkItem(item.id), "Archived")}
        >
          Archive
        </button>,
      );
      break;
    }
    case "not_started": {
      const firstGate = item.chain_definition.nodes.find((n) => n.gate_after)?.gate_after ?? "none";
      title = "waiting to start";
      sub.push("created paused · nothing spends tokens until you start it", `first gate ${firstGate}`);
      row.push(
        <button
          key="start"
          className="btn btn-primary"
          disabled={busy}
          onClick={() => run(() => api.resumeWorkItem(item.id), "Started")}
        >
          <Play size={14} /> Start
        </button>,
        <button key="chain" className="btn btn-secondary" onClick={onEditChain}>
          Edit chain
        </button>,
      );
      break;
    }
  }

  let composer: ReactNode = null;
  switch (open) {
    case "steer":
      composer = (
        <Composer
          title={`Steer ${node}`}
          explanation="the note leads attempt 2's system prompt"
          value={text}
          onChange={setText}
          busy={busy}
          error={err}
          placeholder="What should the next attempt know?"
          footnote="attempt 2 restarts the node's tasks · fix-loop progress is kept"
          submitLabel="Resume with this steer"
          onSubmit={() => submit(() => api.resumeWorkItem(item.id, text.trim()), "Steer applied — resumed")}
          onCancel={close}
        />
      );
      break;
    case "steerRetry":
      composer = (
        <Composer
          title={`Steer & retry ${node}`}
          explanation="the note carries into the retry's system prompt"
          value={text}
          onChange={setText}
          busy={busy}
          error={err}
          placeholder="What should change before the retry?"
          footnote="retry resets the loop counter; steer text carries into cycle 1"
          submitLabel="Steer & retry"
          onSubmit={() => submit(() => api.retryWorkItem(item.id, text.trim() || undefined), "Steer applied — retrying")}
          onCancel={close}
        />
      );
      break;
    case "budget":
      composer = (
        <BudgetComposer
          itemId={item.id}
          capUsd={item.budget?.cap_usd ?? 0}
          spentUsd={item.budget?.spent_usd}
          busy={busy}
          err={err}
          run={submit}
          onCancel={close}
        />
      );
      break;
    case "answer":
      composer = (
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
          onSubmit={() => submit(() => api.resumeWorkItem(item.id, text.trim()), "Answered — resumed")}
          onCancel={close}
        />
      );
      break;
    case "reject": {
      const target = rejectTarget(item, gate);
      composer = (
        <Composer
          title={`Reject ${gate}`}
          value={text}
          onChange={setText}
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
          disabled={text.trim() === ""}
          onSubmit={() =>
            submit(() => api.rejectGate(item.id, gate, text), `Rejected — re-running from ${target ?? gate}`)
          }
          onCancel={close}
        />
      );
      break;
    }
    case "skip":
      composer = (
        <Composer
          title={`Skip ${node ?? gate}`}
          explanation="continue without approving"
          value={text}
          onChange={setText}
          busy={busy}
          error={err}
          placeholder="Why skip this? (optional)"
          submitLabel="Skip step"
          onSubmit={() => submit(() => api.skipWorkItem(item.id, text.trim() || undefined), "Skipped")}
          onCancel={close}
        />
      );
      break;
    case "escalate":
      // Escalate and Reply share it (18): turn > 1 shows the thread above.
      composer = (
        <>
          {escalationTurns.length > 0 && (
            <div className="escalation-thread" data-testid="escalation-thread">
              {escalationTurns.map((s) => {
                const sent = events.find((e) => e.type === "escalation_message" && e.payload.session_id === s.id);
                return (
                  <p key={s.id} className="field-hint">
                    turn {s.attempt}
                    {sent?.payload.auto ? " (auto-escalated)" : ""}: {String(sent?.payload.message ?? "")}
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
            onSubmit={() => submit(() => api.escalateWorkItem(item.id, text.trim()), "Sent to the agent")}
            onCancel={close}
          />
        </>
      );
      break;
  }
  // m08: a composer is a full-screen page on phone. Reject stays inline in the
  // card there, as it always has.
  if (phone && open && open !== "reject") {
    composer = (
      <PhoneComposer title={COMPOSER_TITLES[open]} context={`${item.title} · ${node ?? "—"}`} onCancel={close}>
        {composer}
      </PhoneComposer>
    );
  }

  const [archive, worktree, copyId, copyLink] = itemMenuItems(item);
  const mr = item.mr_ref;
  // The peek's menu is the item page's plus Open item → first (I.2).
  const openItem: OverflowItem[] = variant === "peek" && onOpenItem ? [{ label: "Open item →", onSelect: onOpenItem }] : [];
  const more: OverflowItem[] = [
    ...openItem,
    ...stateItems.map((it, i) => (i === 0 && openItem.length ? { ...it, divider: true } : it)),
    { label: "Review changes", divider: stateItems.length + openItem.length > 0, onSelect: onReviewChanges },
    mr
      ? { label: "Open MR", icon: <ArrowSquareOut size={14} />, onSelect: () => window.open(mr.url, "_blank", "noreferrer") }
      : { label: "Open MR", icon: <ArrowSquareOut size={14} />, disabled: true, hint: "not opened yet", onSelect: () => {} },
    worktree,
    { ...copyId, divider: true },
    copyLink,
    archive,
    // Wherever the item can still be abandoned (W0.9).
    ...(item.status !== "completed" && item.status !== "abandoned" && item.status !== "active"
      ? [
          {
            label: cancelling ? "Cancelling…" : "Cancel work item",
            danger: true,
            divider: true,
            confirm: "Abandon this work item? This reclaims its worktree and destroys any uncommitted work.",
            onSelect: cancelItem,
          },
        ]
      : []),
  ];

  const subParts = sub.filter((p) => p !== null && p !== undefined && p !== false && p !== "");
  const testId = state === "gate" ? "gate-card" : state === "escalated" ? "escalated-card" : "item-card";
  const rowErr = sideErr ?? (open ? null : err);

  return (
    <div
      className={`card item-card${needsYou || state === "escalating" ? " attention-card" : ""}${state === "gate" ? " gate-card" : ""}`}
      data-state={state}
      data-gate={state === "gate" ? gate : undefined}
      data-testid={testId}
    >
      <div className="item-card-head">
        <StatusGlyph status={state} />
        <div className="item-card-text">
          <span className="item-card-title">{title}</span>
          {subParts.length > 0 && (
            <span className="item-card-sub">
              {subParts.map((p, i) => (
                <Fragment key={i}>
                  {i > 0 && " · "}
                  {p}
                </Fragment>
              ))}
            </span>
          )}
        </div>
        {variant === "page" && (
          <span className="item-card-stats" title={stat.title}>
            {stat.text}
          </span>
        )}
      </div>
      {body}
      <div className="item-card-actions">
        {row}
        {/* W6.3: between the server saying yes and the store re-reading the
            item, the card says so instead of offering the action again. */}
        {pending && <span className="field-hint action-pending">pending…</span>}
        <OverflowMenu label="More actions" text wide items={more} />
      </div>
      {rowErr && <p className="form-error">{rowErr}</p>}
      {composer}
      {state === "rate_limited" && <RateLimitRow item={item} />}
    </div>
  );
}
