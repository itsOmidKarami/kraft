import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ChatText, FolderOpen, Pause, Prohibit } from "@phosphor-icons/react";
import * as api from "../api";
import { ArtifactModal } from "../components/ArtifactModal";
import { BudgetCard } from "../components/BudgetCard";
import { CappedCard } from "../components/CappedCard";
import { CurrentNodePanel } from "../components/CurrentNodePanel";
import { DiffModal } from "../components/DiffModal";
import { Escalate } from "../components/Escalate";
import { EventTimeline } from "../components/EventTimeline";
import { ARTIFACT_LABELS, Gate } from "../components/Gate";
import { LinkedDocuments } from "../components/LinkedDocuments";
import { PausedCard } from "../components/PausedCard";
import { ChainBar, Row, RowState, RowText, StatusGlyph, Tabs } from "../components/ui";
import { elapsed, repoName, statusWord, tokens, usd } from "../format";
import { useStore } from "../store";
import type { KraftEvent, SessionStatus, WorkerSession, WorkItem } from "../types";

/** Whether this browser is on the machine Kraft runs on. Loopback is the one
 * origin that cannot be remote, and the worktree path only means something to
 * a machine that has it — so "Open worktree" is offered here and nowhere else. */
/** repos-panel state -> the glyph/label vocabulary session rows already use,
 * so a "failed" repo (Kraft-qlsf) renders as failed, not merely pending. */
function repoGlyphStatus(state: string): SessionStatus {
  if (state === "merged") return "done";
  if (state === "failed") return "failed";
  if (state === "open") return "running";
  return "pending";
}

function onServerMachine(): boolean {
  return ["localhost", "127.0.0.1", "[::1]", "::1"].includes(window.location.hostname);
}

/** How long the current node has been running, from its last node_started. */
function nodeRuntime(events: KraftEvent[], nodeId: string | null): string | null {
  if (!nodeId) return null;
  const start = [...events]
    .reverse()
    .find((e) => e.type === "node_started" && e.payload.node_id === nodeId);
  if (!start) return null;
  const ms = Date.now() - Date.parse(start.created_at);
  return Number.isNaN(ms) ? null : elapsed(ms);
}

/** "3 tasks · 41.2k tokens this node · 138k total · $2.41" (design 3b). */
function usageLine(item: WorkItem, taskCount: number): string {
  const parts = [`${taskCount} task${taskCount === 1 ? "" : "s"}`];
  const u = item.usage;
  if (u) {
    const node = u.by_node.find((n) => n.node === item.current_node_id);
    const sum = (r?: { tokens_in: number; tokens_out: number }) =>
      r ? r.tokens_in + r.tokens_out : 0;
    if (node) parts.push(`${tokens(sum(node))} tokens this node`);
    parts.push(`${tokens(sum(u.total))} total`);
    // only what agents actually reported; a "+" marks a sum still missing some
    if (u.total.cost_usd > 0) parts.push(usd(u.total.cost_usd, u.total.cost_complete));
  }
  return parts.join(" · ");
}

/**
 * The needs_context answer card — a `needs_human` stop whose reason is an
 * agent's question, answerable the same way a pause is (spec §3). Unlike
 * `PausedCard` this state has no prior session to prefill a note from and no
 * "attempt N" to relaunch: just the question and a required answer.
 */
function NeedsContextCard({
  item,
  sessions,
}: {
  item: WorkItem;
  sessions: WorkerSession[];
}) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.resumeWorkItem(item.id, answer.trim());
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
          Answer{" "}
          <span className="field-hint">· goes into the next attempt's system prompt</span>
        </label>
        <textarea
          id="needs-context-answer"
          className="input"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
        />
      </div>
      <div className="gate-actions capped-actions">
        <button
          className="btn btn-primary"
          disabled={busy || answer.trim() === ""}
          onClick={submit}
        >
          <ChatText size={14} />
          Answer
        </button>
        <Escalate item={item} sessions={sessions} />
      </div>
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}

/** The label. Read-only until asked, the same read-then-edit shape as
 *  `Description` below. Deliberately not sharing a component with it: two
 *  callers, one field each, and the two edits mean different enough things
 *  that the server records them as different events. */
function Title({ item }: { item: WorkItem }) {
  const hydrateItem = useStore((s) => s.hydrateItem);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.title);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.updateWorkItem(item.id, { title: draft });
      await hydrateItem(item.id);
      setEditing(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (editing) {
    return (
      <div className="field">
        <label htmlFor="item-title-edit">Title</label>
        <input
          id="item-title-edit"
          className="input"
          aria-label="title"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <div className="gate-actions capped-actions">
          <button className="btn btn-primary" disabled={busy || !draft.trim()} onClick={save}>
            Save
          </button>
          <button className="btn" disabled={busy} onClick={() => setEditing(false)}>
            Cancel
          </button>
        </div>
        {err && <p className="form-error">{err}</p>}
      </div>
    );
  }

  return (
    // The button is a sibling, not a child: `.detail h2` is asserted with an
    // exact-text match across most of the e2e suite (it's the one element
    // that names which work item is on screen), so anything nested inside it
    // — however it renders visually — breaks every one of those assertions.
    <div className="control-row" data-testid="item-title">
      <h2 className="detail-title">{item.title}</h2>
      <button
        className="btn btn-quiet"
        onClick={() => {
          setDraft(item.title);
          setEditing(true);
        }}
      >
        Edit
      </button>
    </div>
  );
}

/** The brief. Read-only until asked, because editing it changes what every
 *  later node is told — the same reason the edit is recorded as an event. */
function Description({ item }: { item: WorkItem }) {
  const hydrateItem = useStore((s) => s.hydrateItem);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.description ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.updateWorkItem(item.id, { description: draft });
      await hydrateItem(item.id);
      setEditing(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (editing) {
    return (
      <div className="field">
        <label htmlFor="item-description-edit">Description</label>
        <textarea
          id="item-description-edit"
          className="input"
          aria-label="description"
          rows={4}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <div className="gate-actions capped-actions">
          <button className="btn btn-primary" disabled={busy} onClick={save}>
            Save
          </button>
          <button className="btn" disabled={busy} onClick={() => setEditing(false)}>
            Cancel
          </button>
        </div>
        {err && <p className="form-error">{err}</p>}
      </div>
    );
  }

  if (!item.description) {
    return (
      <button className="btn btn-quiet" onClick={() => setEditing(true)}>
        Add a description
      </button>
    );
  }

  return (
    <p className="detail-description" data-testid="item-description">
      {item.description}
      <button
        className="btn btn-quiet"
        onClick={() => {
          setDraft(item.description ?? "");
          setEditing(true);
        }}
      >
        Edit
      </button>
    </p>
  );
}

const STATUS_TAG: Record<WorkItem["status"], string> = {
  active: "tag tag-outline",
  completed: "tag tag-neutral",
  needs_human: "tag tag-accent",
  paused: "tag tag-neutral",
  // Same muted treatment as completed: both are terminal, and neither is a
  // state the reader needs drawn to.
  abandoned: "tag tag-neutral",
  // Same treatment as active: the poller is driving it, not waiting on a
  // person -- the same story Board.tsx's "Running" group tells for it.
  rate_limited: "tag tag-outline",
};

export function WorkItemDetail() {
  const { id = "" } = useParams();
  const item = useStore((s) => s.workItems[id]);
  const sessions = useStore((s) => s.sessionsByItem[id] ?? []);
  const events = useStore((s) => s.eventsByItem[id] ?? []);
  const hydrateItem = useStore((s) => s.hydrateItem);
  const [tab, setTab] = useState("tasks");
  // Pause is a request, not a state change: the button waits for the
  // worker_session_paused event rather than pretending it already landed.
  const [pausing, setPausing] = useState(false);
  const [pauseErr, setPauseErr] = useState<string | null>(null);
  // Cancel is the only door onto /abandon (Kraft-aayj) -- an item stuck at a
  // gate had no way to be cleared except a terminal. Left disabled while
  // active: the API itself refuses an abandon then (409, "pause it before
  // abandoning"), and only after Kraft-41b does pause reliably stop the
  // current node rather than silently no-op through a live ci_poll wait --
  // wiring this straight through active would abandon the worktree out from
  // under whatever is still running.
  const [cancelling, setCancelling] = useState(false);
  const [cancelErr, setCancelErr] = useState<string | null>(null);
  // A failed load must not look like an item with nothing in it: without this,
  // the screen renders every panel empty and offers the controls for the wrong
  // state, which is exactly how a shell-cached deep link presented itself.
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const [showArtifact, setShowArtifact] = useState(false);
  const [worktreeErr, setWorktreeErr] = useState<string | null>(null);
  const local = onServerMachine();

  // Keyed on the event existing, not on the current node being one of
  // {open_mr, mr_checks, human_review, merge}: the link is correct exactly
  // when an MR exists, it survives a rejection walking the item back to
  // implementation, and it is one condition instead of a set to keep in step
  // with the chain template (Kraft-d2sq, a deliberate deviation from the
  // bead's wording with the same intent).
  const mr = [...events].reverse().find((e) => e.type === "mr_opened");

  useEffect(() => {
    if (item?.status === "paused") setPausing(false);
  }, [item?.status]);

  useEffect(() => {
    const load = () =>
      hydrateItem(id).then(
        () => setLoadErr(null),
        (e) => setLoadErr(e instanceof Error ? e.message : String(e)),
      );
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [id, hydrateItem]);

  if (loadErr && !item) {
    return (
      <p className="empty" role="alert">
        could not load this work item — {loadErr}
      </p>
    );
  }
  if (!item) return <p className="empty">unknown work item</p>;

  const nodes = item.chain_definition.nodes;
  const at = nodes.findIndex((n) => n.id === item.current_node_id);
  const runtime = nodeRuntime(events, item.current_node_id);
  const gate = item.pending_gate ?? null;
  const nodeSessions = sessions.filter((s) => s.node_id === item.current_node_id);
  // Every non-gate `needs_human` stop, whatever put it there: a cap breach, a
  // `no_progress` escalation (which carries `capped: null`, because the
  // executor never fakes a cap it did not hit), or a plain task failure. All
  // of them leave retry as the only door — resume wants `paused` and pause
  // wants `running` — so gating this on `cappedOut`, or on the node having a
  // fix_loop, is what took the control off the screen for the stops that
  // needed it most (Kraft-bzwi).
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
    if (
      !window.confirm(
        "Abandon this work item? This reclaims its worktree and destroys any uncommitted work.",
      )
    ) {
      return;
    }
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
    <div className="detail">
      <div className="detail-head">
        <div className="detail-meta">
          <span title={item.repo}>{repoName(item.repo)}</span>
          {!!item.repos?.length && (
            <span className="tag tag-neutral tag-tight">
              +{item.repos.length - 1} submodules
            </span>
          )}
          <span>{item.chain_template}</span>
          {mr && (
            <a className="mr-link" href={String(mr.payload.url)} target="_blank" rel="noreferrer">
              !{String(mr.payload.number)}
            </a>
          )}
          {item.bead_id && <code title={`work item ${item.id}`}>{item.bead_id}</code>}
          {item.attachments?.length ? (
            <span className="tag tag-outline tag-tight">
              from {item.attachments.map((a) => a.kind).join("+")}
            </span>
          ) : null}
          <span className={`${STATUS_TAG[item.status]} detail-status`}>
            {statusWord(item.status)}
          </span>
        </div>
        <Title item={item} />
        <Description item={item} />
        <div className="detail-hero">
          <span className="hero-node">{item.current_node_id ?? "—"}</span>
          {item.fixCycle != null && (
            <span className="tag tag-outline">fix · cycle {item.fixCycle}</span>
          )}
          {item.cappedOut && (
            <span className="tag tag-outline">
              <Prohibit size={11} />
              capped {item.cappedOut.cycles}/{item.cappedOut.attempts}
            </span>
          )}
          <span className="hero-sub">
            {at >= 0 && `node ${at + 1} of ${nodes.length}`}
            {/* "running 6s" on an item that stopped an hour ago is a lie the
                clock keeps telling. Only an active item is running; anything
                else gets the neutral "for", which is true in every state. */}
            {runtime && (item.status === "active" ? ` · running ${runtime}` : ` · ${runtime}`)}
          </span>
        </div>
      </div>

      <ChainBar
        nodes={item.chain_definition.nodes}
        currentNodeId={item.current_node_id}
        done={item.completedNodes}
        size="lg"
      />

      {/* Multi-repo only (design 3a): a single-repo item's `repos` is empty. */}
      {!!item.repos?.length && (
        <section className="repos-panel">
          <div className="repos-head">
            <span className="section-label">Repos</span>
            <span>merge rank · deepest first</span>
            <span className="repos-policy">
              root_merge_policy: <b>{item.root_merge_policy}</b>
            </span>
          </div>
          {item.repos.map((r) => (
            <Row key={r.path} columns="22px 1fr 100px auto" data-repo={r.path}>
              <StatusGlyph status={repoGlyphStatus(r.state)} />
              <RowText
                title={r.repo}
                sub={
                  <>
                    <code>{r.path}</code> · merge rank {r.merge_rank}
                  </>
                }
              />
              <span className="row-sub">{r.role}</span>
              <RowState status={repoGlyphStatus(r.state)}>{r.state}</RowState>
            </Row>
          ))}
        </section>
      )}

      {/* Available at any status but a terminal one, gate or no gate -- unlike
          Pause below, which only makes sense for the one status it is scoped
          to. This is the fix for a real stuck item: one parked at a gate with
          nothing local to steer had no way to be cleared except a terminal
          (Kraft-aayj). */}
      {item.status !== "completed" && item.status !== "abandoned" && (
        <div className="control-row desktop-only">
          <button
            className="btn btn-ghost"
            disabled={cancelling || item.status === "active"}
            onClick={cancel}
          >
            <Prohibit size={14} />
            {cancelling ? "Cancelling…" : "Cancel"}
          </button>
          <span className="control-hint">
            {cancelErr ??
              (item.status === "active"
                ? "pause it before abandoning"
                : "reclaims the worktree and destroys uncommitted work")}
          </span>
        </div>
      )}

      {/* Phone (design 1n): a gate is actionable anywhere, because it is the one
          thing that is waiting on a person. Everything else is read-only here. */}
      {!gate && <p className="phone-only open-on-desktop">Open on desktop to steer or retry.</p>}

      {/* Checked first: a `needs_context` stop is answerable through /resume
          regardless of the current node's shape, and `stranded`
          below fires for *any* non-gate needs_human — including this one,
          since `mark_needs_human` never sets `capped` for a needs_context
          reason. Routing it through CappedCard's "Steer and
          retry" would go through /retry instead, which resets the loop
          counter and discards fix-loop progress spec §3 says must survive. */}
      {item.status === "needs_human" && item.needs_context_question && !gate ? (
        <div className="desktop-only">
          <NeedsContextCard item={item} sessions={sessions} />
        </div>
      ) : /* Then budget: a spend cap stopped the item before it launched
             anything, and `stranded` below would otherwise claim the node
             needs a steer when no agent ever ran. The two are
             mutually exclusive in practice — a budget stop's reason is never
             a needs_context question — so this order only decides which card
             wins if that ever stops being true. */
      item.budget ? (
        <div className="desktop-only">
          <BudgetCard item={item} sessions={sessions} />
        </div>
      ) : item.cappedOut || stranded ? (
        <div className="desktop-only">
          <CappedCard item={item} sessions={sessions} events={events} />
        </div>
      ) : item.status === "paused" ? (
        <div className="desktop-only">
          <PausedCard item={item} sessions={sessions} />
        </div>
      ) : gate ? (
        <Gate
          item={item}
          gate={gate}
          sessions={sessions}
          sub={`${nodeSessions.map((s) => s.hook_point).join(", ")} completed clean`}
          artifact={
            /* Both, not one (Kraft-yytk): the brief is the agent's argument,
               the diff is the evidence, and a reviewer needs to check one
               against the other. Every other gate has only a document. */
            (item.gate_artifact || gate === "human_review_approval") && (
              <div className="gate-artifacts">
                {item.gate_artifact && (
                  <button className="btn btn-secondary" onClick={() => setShowArtifact(true)}>
                    {ARTIFACT_LABELS[gate] ?? "Review document"}
                  </button>
                )}
                {gate === "human_review_approval" && (
                  <button className="btn btn-secondary" onClick={() => setShowDiff(true)}>
                    Review changes
                  </button>
                )}
              </div>
            )
          }
          deferred={gate === "human_review_approval" ? item.deferred_findings : undefined}
          // Not gate-specific, unlike `deferred`: spec §2 puts concerns at the
          // next gate the item reaches, whatever that gate is. Only
          // `on.implementation.start` is bound to `kind: agent` in the shipped
          // registry.yaml, but that file is operator-editable from Settings.
          concerns={item.concerns}
        />
      ) : (
        <div className="control-row desktop-only">
          <button
            className="btn btn-secondary"
            disabled={pausing || item.status !== "active"}
            onClick={pause}
          >
            <Pause size={14} />
            {pausing ? "Pausing…" : "Pause"}
          </button>
          {/* Steer is disabled, not hidden, on a steerable hook that is still
              running; hiding it belongs to the registry's `interactive` flag,
              which lands with the settings API (Kraft-7z6.14). */}
          <button className="btn btn-ghost" disabled>
            <ChatText size={14} />
            Steer
          </button>
          <span className="control-hint">
            {pauseErr ?? "steer unlocks once paused"}
          </span>
          <span className="control-usage">{usageLine(item, nodeSessions.length)}</span>
        </div>
      )}

      {/* "Review changes" is reachable at any status/gate: the one review path
          the Gate artifact doesn't already cover for `human_review_approval`.
          "Open worktree" sits next to it (sub-project A spec §4) and only on
          the server's own machine — the path is a local one. */}
      {(gate !== "human_review_approval" || local) && (
        <div className="control-row">
          {gate !== "human_review_approval" && (
            <button className="btn btn-secondary" onClick={() => setShowDiff(true)}>
              Review changes
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
              Open worktree
            </button>
          )}
          {worktreeErr && <span className="control-hint">{worktreeErr}</span>}
        </div>
      )}

      {showDiff && <DiffModal workItemId={item.id} onClose={() => setShowDiff(false)} />}
      {showArtifact && (
        <ArtifactModal workItemId={item.id} onClose={() => setShowArtifact(false)} />
      )}

      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          // the tab shows every session now (Kraft-n9gw), so the count is
          // every session. `usageLine`'s task count stays current-node scoped:
          // it sits on the control row and is a statement about now.
          { id: "tasks", label: "Tasks", count: sessions.length },
          { id: "timeline", label: "Timeline", count: events.length },
          { id: "documents", label: "Documents" },
        ]}
      />

      <div className="tab-body">
        {tab === "tasks" && <CurrentNodePanel item={item} sessions={sessions} />}
        {tab === "timeline" && <EventTimeline events={events} sessions={sessions} />}
        {tab === "documents" && (
          <LinkedDocuments workItemId={id} eventCount={events.length} />
        )}
      </div>
    </div>
  );
}
