import { Fragment, useEffect, useRef, useState } from "react";
import { Eye, FileText, ListChecks, Notebook, Paperclip, Robot } from "@phosphor-icons/react";
import * as api from "../../../api";
import { ago, cleanTitle, docTitle, runLabel } from "../../../format";
import type { ChainNode, WorkerSession, WorkItemDocument } from "../../../types";
import { GATE_DOC_ID } from "../selection";
import { ScopeChips, type Scope } from "./Tasks";

/**
 * Inspector · Documents (UI v2 · 05, 14; W11 · G; W13 · B): the gate's document
 * pinned first, then what the selected node wrote, then one folded row per
 * other node with documents -- or every node open, in chain order, under
 * "all". A filter searches every node whatever the scope. Selecting a row is
 * what `RightPane/Doc.tsx` shows.
 */

const KIND_ICONS: Record<string, typeof FileText> = {
  plans: ListChecks,
  specs: FileText,
  reviews: Eye,
  sessions: Notebook,
};

const KIND_LABELS: Record<string, string> = { specs: "spec", plans: "plan", reviews: "review", sessions: "session" };

/** One small kind label per row (G.3). */
function kindLabel(d: WorkItemDocument): string {
  if (d.hook_point === "escalation") return "escalation";
  return KIND_LABELS[d.kind ?? ""] ?? d.kind ?? d.source_kind;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** What a folded node holds: `1 review · 2 sessions`. */
function kindCounts(docs: WorkItemDocument[]): string {
  const counts = new Map<string, number>();
  for (const d of docs) counts.set(kindLabel(d), (counts.get(kindLabel(d)) ?? 0) + 1);
  return [...counts].map(([kind, n]) => plural(n, kind)).join(" · ");
}

const OTHER = "other";
const nodeOf = (d: WorkItemDocument) => d.node_id ?? OTHER;
const isRun = (d: WorkItemDocument) => d.source_kind === "session_summary";

export function Documents({
  workItemId,
  eventCount,
  selected,
  onSelect,
  preselectPath,
  gatePending,
  gateArtifactPending,
  nodeId = null,
  nodes = [],
  scope = "node",
  onScope,
  onCount,
  item = null,
  sessions = [],
}: {
  workItemId: string;
  eventCount: number;
  selected: string | null;
  onSelect: (documentId: string) => void;
  /** The gate card's "Read <doc>" (06) names a specific artifact by repo
   *  path (`item.gate_artifact`) — preferred over "just pick the first
   *  document" once the list lands with a matching row, and pinned above
   *  everything else (G5-05, G.2), "not written yet" when the gate has none. */
  preselectPath?: string | null;
  /** Whether a gate is pending at all. `item.gate_artifact` is null both when
   *  there is no gate and when the gate's artifact is not yet written to disk
   *  (board.py's `gate_artifact`), so the "Gate document" section can't guard
   *  on `preselectPath` being present — it must guard on the gate itself. */
  gatePending: boolean;
  /** Whether a gate is pending and its artifact exists on disk. NOT derivable
   *  from the document list: the index does not ingest a gate's artifact until
   *  approval (`artifacts._ingest_approved_gate_artifact`), so "absent from
   *  `docs`" is the normal state of a written artifact (G5-05). */
  gateArtifactPending: boolean;
  /** The stage graph's selected node: "this node" is its documents. */
  nodeId?: string | null;
  /** The chain, for the order nodes are listed in. */
  nodes?: ChainNode[];
  scope?: Scope;
  /** The this node · all switch, shared with Tasks and Timeline (W11 · F). */
  onScope?: (s: Scope) => void;
  /** The tab's count, which follows the scope (G.4). */
  onCount?: (n: number) => void;
  /** Whose documents: its title and bead id come out of a row's title (B.3). */
  item?: { title?: string | null; bead_id?: string | null } | null;
  /** The item's sessions, for the newest-run-first order (B.6). */
  sessions?: WorkerSession[];
}) {
  const [docs, setDocs] = useState<WorkItemDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set());

  useEffect(() => {
    let live = true;
    api
      .getWorkItemDocuments(workItemId)
      .then((r) => {
        if (!live) return;
        setDocs(r.documents);
        setError(null);
      })
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [workItemId, eventCount]);

  // B.1/B.3: what a row says. A summary leads with its hook and run; its title,
  // cleaned, is the second line -- dropped when it is only an id (W11 · H: the
  // hook already names it) or nothing is left once cleaned.
  const run = (d: WorkItemDocument) => (isRun(d) ? runLabel(d.hook_point, d.attempt, d.round) : null);
  const secondLine = (d: WorkItemDocument) => {
    const t = docTitle(d);
    return t.startsWith("Session · ") ? "" : cleanTitle({ title: t }, item);
  };

  // B.6: newest run on top -- the session's created_at when the store has it.
  const createdAt = new Map(sessions.map((s) => [s.id, s.created_at]));
  const when = (d: WorkItemDocument) => (d.worker_session_id && createdAt.get(d.worker_session_id)) || d.indexed_at;
  const sorted = [...(docs ?? [])].sort((a, b) => when(b).localeCompare(when(a)));

  const gateDoc = preselectPath ? (sorted.find((d) => d.path === preselectPath) ?? null) : null;
  const rest = sorted.filter((d) => d !== gateDoc);
  // Chain order, then any node the chain does not name (and documents with none).
  const order = [...nodes.map((n) => n.id)];
  for (const d of rest) if (!order.includes(nodeOf(d))) order.push(nodeOf(d));
  const q = filter.trim().toLowerCase();
  // B.5: hook, run label, cleaned title, path.
  const shown = q
    ? rest.filter((d) =>
        [d.hook_point, run(d), isRun(d) ? secondLine(d) : docTitle(d), d.path].some((f) => f?.toLowerCase().includes(q)),
      )
    : rest;
  const groups = order
    .map((id) => ({ id, docs: shown.filter((d) => nodeOf(d) === id) }))
    .filter((g) => g.docs.length > 0);
  const own = groups.find((g) => g.id === nodeId);
  const others = groups.filter((g) => g !== own);
  // A filter searches every node, whatever the scope, so all its matches show.
  // With no node selected there is no "this node" to scope to: everything
  // shows, the way Tasks does.
  const allShown = scope === "all" || !nodeId;
  const expandAll = allShown || !!q;

  const gateRows = gatePending ? 1 : 0;
  const count = allShown ? rest.length + gateRows : rest.filter((d) => nodeOf(d) === nodeId).length + gateRows;
  useEffect(() => {
    if (docs) onCount?.(count);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docs, count]);

  // Default the selection once the list lands, so the right pane never sits
  // empty for a tab that has content: the gate's own artifact by path when
  // one was asked for, this node's first document otherwise. `autoPickRef`
  // marks a selection this effect made itself (not a row click) -- a gate's
  // artifact can land in the index a scan behind the gate appearing, so an
  // earlier auto-pick has to be free to upgrade once the real match shows up;
  // a person's own click never should.
  //
  // When a preselectPath is given, a miss must not fall back to another
  // document: a gate's artifact isn't ingested until approval, so "no match
  // yet" is the normal pre-approval state. On a miss we select GATE_DOC_ID and
  // the right pane reads it out of the worktree (Kraft-8ic7).
  const autoPickRef = useRef<string | null>(null);
  useEffect(() => {
    if (!docs) return; // still loading
    if (selected && selected !== autoPickRef.current) return; // a real row click
    if (preselectPath) {
      const match = docs.find((d) => d.path === preselectPath);
      if (!match) {
        if (gateArtifactPending && selected !== GATE_DOC_ID) {
          autoPickRef.current = GATE_DOC_ID;
          onSelect(GATE_DOC_ID);
        }
        return;
      }
      if (match.document_id === selected) return;
      autoPickRef.current = match.document_id;
      onSelect(match.document_id);
      return;
    }
    const pick = (docs.find((d) => d.node_id === nodeId) ?? docs[0])?.document_id;
    if (!pick || pick === selected) return;
    autoPickRef.current = pick;
    onSelect(pick);
  }, [docs, selected, onSelect, preselectPath, gateArtifactPending, nodeId]);

  const toggle = (id: string) =>
    setOpen((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // B.1/B.2: no node on any row -- the node is the section header. The path is
  // the row's tooltip and the pane header's; the session id is the pane
  // header's, never row text.
  const row = (d: WorkItemDocument, gate = false) => {
    const Icon = d.hook_point === "escalation" ? Robot : (KIND_ICONS[d.kind ?? ""] ?? FileText);
    const time = ago(d.indexed_at);
    const attached = d.attachment_kind && (
      <span className="doc-attached" title="attached at intake" aria-label="attached at intake" role="img">
        <Paperclip size={11} aria-hidden />
      </span>
    );
    let text;
    if (isRun(d)) {
      const label = run(d);
      const second = secondLine(d);
      text = (
        <span className="doc-text">
          <span className="doc-title doc-run">
            {d.hook_point ?? kindLabel(d)}
            {label && ` · ${label}`}
            {time && <span className="doc-time"> · {time}</span>}
          </span>
          {second && <span className="doc-sub doc-line2">{second}</span>}
        </span>
      );
    } else {
      const sub = [d.hook_point, time].filter(Boolean).join(" · ");
      text = (
        <span className="doc-text">
          {/* W11 · H: never a bare id. */}
          <span className="doc-title">{docTitle(d)}</span>
          <span className="doc-sub">
            {sub}
            {attached && (
              <>
                {sub && " · "}
                {attached}
              </>
            )}
          </span>
        </span>
      );
    }
    return (
      <button
        key={d.document_id}
        className="doc-row"
        data-document-id={d.document_id}
        data-gate={gate || undefined}
        data-selected={d.document_id === selected}
        title={d.path}
        onClick={() => onSelect(d.document_id)}
      >
        <Icon size={16} className="doc-icon" />
        {text}
        <span className="tag tag-neutral doc-kind">{kindLabel(d)}</span>
      </button>
    );
  };

  const section = (id: string) => (
    <p className="section-label">{id === nodeId ? `${id} · written by this node` : id}</p>
  );

  return (
    <div className="inspector-list linked-docs" data-testid="inspector-documents">
      <p className="section-label">
        DOCUMENTS · {count}
        {onScope && <ScopeChips scope={scope} onScope={onScope} nodeId={nodeId} />}
      </p>
      <input
        className="input doc-filter"
        type="search"
        aria-label="filter documents"
        placeholder="filter documents…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      {error && <p className="form-error" role="alert">{error}</p>}
      {!error && docs?.length === 0 && <p className="empty">no linked documents yet</p>}

      {gatePending && (
        <>
          <p className="section-label">Gate document</p>
          {gateDoc ? (
            row(gateDoc, true)
          ) : (
            <button
              className="doc-row"
              data-gate
              data-selected={selected === GATE_DOC_ID}
              disabled={!gateArtifactPending}
              title={preselectPath ?? undefined}
              onClick={() => onSelect(GATE_DOC_ID)}
            >
              <FileText size={16} className="doc-icon" />
              <span className="doc-text">
                <span className="doc-title path">{preselectPath}</span>
                <span className="doc-sub">{gateArtifactPending ? "not indexed yet" : "not written yet"}</span>
              </span>
            </button>
          )}
        </>
      )}

      {q && shown.length === 0 && docs && docs.length > 0 && <p className="empty">no documents match “{filter.trim()}”</p>}

      {expandAll
        ? groups.map((g) => (
            <Fragment key={g.id}>
              {section(g.id)}
              {g.docs.map((d) => row(d))}
            </Fragment>
          ))
        : (
          <>
            {own && (
              <>
                {section(own.id)}
                {own.docs.map((d) => row(d))}
              </>
            )}
            {others.map((g) => (
              <Fragment key={g.id}>
                <button className="doc-row doc-fold" aria-expanded={open.has(g.id)} onClick={() => toggle(g.id)}>
                  <span className="doc-fold-caret" aria-hidden>
                    {open.has(g.id) ? "▾" : "▸"}
                  </span>
                  <span className="doc-fold-label">
                    {g.id} · {kindCounts(g.docs)}
                  </span>
                  <span className="doc-fold-count">{g.docs.length}</span>
                </button>
                {open.has(g.id) && g.docs.map((d) => row(d))}
              </Fragment>
            ))}
          </>
        )}

      {!!docs?.length && (
        <p className="inspector-foot">
          Written by agents into <code>.engineering/</code>; the index lags a scan behind git.
        </p>
      )}
    </div>
  );
}
