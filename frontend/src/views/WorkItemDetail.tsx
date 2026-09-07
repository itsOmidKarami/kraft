import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ChatText, FolderOpen, Pause, Prohibit } from "@phosphor-icons/react";
import * as api from "../api";
import { ArtifactModal } from "../components/ArtifactModal";
import { BudgetCard } from "../components/BudgetCard";
import { CappedCard } from "../components/CappedCard";
import { CurrentNodePanel } from "../components/CurrentNodePanel";
import { DiffModal } from "../components/DiffModal";
import { EventTimeline } from "../components/EventTimeline";
import { Gate } from "../components/Gate";
import { LinkedDocuments } from "../components/LinkedDocuments";
import { PausedCard } from "../components/PausedCard";
import { ChainBar, Row, RowState, RowText, StatusGlyph, Tabs } from "../components/ui";
import { elapsed, repoName, statusWord, tokens, usd } from "../format";
import { useStore } from "../store";
import type { KraftEvent, WorkItem } from "../types";

/** Whether this browser is on the machine Kraft runs on. Loopback is the one
 * origin that cannot be remote, and the worktree path only means something to
 * a machine that has it — so "Open worktree" is offered here and nowhere else. */
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
function NeedsContextCard({ item }: { item: WorkItem }) {
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
      </div>
      {err && <p className="form-error">{err}</p>}
    </div>
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
  // A failed load must not look like an item with nothing in it: without this,
  // the screen renders every panel empty and offers the controls for the wrong
  // state, which is exactly how a shell-cached deep link presented itself.
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const [showArtifact, setShowArtifact] = useState(false);
  const [worktreeErr, setWorktreeErr] = useState<string | null>(null);
  const local = onServerMachine();

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
  const currentNode = at >= 0 ? nodes[at] : undefined;
  const runtime = nodeRuntime(events, item.current_node_id);
  const gate = item.pending_gate ?? null;
  const nodeSessions = sessions.filter((s) => s.node_id === item.current_node_id);
  // A `no_progress` escalation stops the item exactly like a cap breach, but
  // with `capped: null` (Kraft's own executor deliberately never fakes a cap
  // it did not hit). Gate on the status + fix_loop, not on cappedOut alone,
  // or the retry control disappears for the one stop where a human's steer
  // is the only way to unstick the item.
  const strandedInFixLoop = !gate && item.status === "needs_human" && !!currentNode?.fix_loop;

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
        <h2 className="detail-title">{item.title}</h2>
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

      <ChainBar item={item} size="lg" />

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
              <StatusGlyph status={r.state === "merged" ? "done" : "pending"} />
              <RowText
                title={r.repo}
                sub={
                  <>
                    <code>{r.path}</code> · merge rank {r.merge_rank}
                  </>
                }
              />
              <span className="row-sub">{r.role}</span>
              <RowState status={r.state === "merged" ? "done" : "pending"}>{r.state}</RowState>
            </Row>
          ))}
        </section>
      )}

      {/* Phone (design 1n): a gate is actionable anywhere, because it is the one
          thing that is waiting on a person. Everything else is read-only here. */}
      {!gate && <p className="phone-only open-on-desktop">Open on desktop to steer or retry.</p>}

      {/* Checked first: a `needs_context` stop is answerable through /resume
          regardless of the current node's shape, and `strandedInFixLoop`
          below fires for *any* needs_human on a fix-loop node — including
          this one, since `mark_needs_human` never sets `capped` for a
          needs_context reason. Routing it through CappedCard's "Steer and
          retry" would go through /retry instead, which resets the loop
          counter and discards fix-loop progress spec §3 says must survive. */}
      {item.status === "needs_human" && item.needs_context_question && !gate ? (
        <div className="desktop-only">
          <NeedsContextCard item={item} />
        </div>
      ) : /* Then budget: a spend cap stopped the item before it launched
             anything, and `strandedInFixLoop` below would otherwise claim a
             fix loop needs a steer when no fix agent ever ran. The two are
             mutually exclusive in practice — a budget stop's reason is never
             a needs_context question — so this order only decides which card
             wins if that ever stops being true. */
      item.budget ? (
        <div className="desktop-only">
          <BudgetCard item={item} />
        </div>
      ) : item.cappedOut || strandedInFixLoop ? (
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
          sub={`${nodeSessions.map((s) => s.hook_point).join(", ")} completed clean`}
          artifact={
            gate === "human_review_approval" ? (
              <button className="btn btn-secondary" onClick={() => setShowDiff(true)}>
                Review changes
              </button>
            ) : item.gate_artifact ? (
              <button className="btn btn-secondary" onClick={() => setShowArtifact(true)}>
                {gate === "spec_approval" ? "Review spec" : "Review plan"}
              </button>
            ) : undefined
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
          { id: "tasks", label: "Tasks", count: nodeSessions.length },
          { id: "timeline", label: "Timeline", count: events.length },
          { id: "documents", label: "Documents" },
        ]}
      />

      <div className="tab-body">
        {tab === "tasks" && <CurrentNodePanel item={item} sessions={sessions} />}
        {tab === "timeline" && <EventTimeline events={events} />}
        {tab === "documents" && (
          <LinkedDocuments workItemId={id} eventCount={events.length} />
        )}
      </div>
    </div>
  );
}
