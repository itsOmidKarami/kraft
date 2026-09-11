import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Check, CirclesThree, MagnifyingGlass } from "@phosphor-icons/react";
import * as api from "../api";
import { repoName } from "../format";
import { backdropProps } from "../useModal";
import { SectionLabel } from "./ui";
import { useStore } from "../store";
import { SETTINGS_NAV } from "../settingsNav";
import type { Bead, SearchResult } from "../types";
import { DocumentModal } from "./DocumentModal";
import { Snippet } from "./Snippet";

/**
 * The ⌘K palette (UI v2 · 09). Sections, in order: Actions (pending gates
 * first), Work items, Documents (the pre-existing indexed-artifact search,
 * relabelled), Go to (settings pages and other screens). One flat list under
 * the hood — `rows` — so ↑/↓/Enter can move across sections without four
 * separately-indexed arrays.
 */

const uniq = (xs: string[]) => [...new Set(xs)].sort();
const MODES = ["hybrid", "fts", "vector"];

const GOTO_PAGES = [
  { label: "Board", to: "/" },
  { label: "Analytics", to: "/analytics" },
  ...SETTINGS_NAV.map((n) => ({ label: n.label, to: `/settings/${n.to}` })),
];

type Row =
  | { kind: "action"; key: string; label: string; sub: string; act: () => void }
  | { kind: "workitem"; key: string; label: string; sub: string; to: string }
  | { kind: "document"; key: string; result: SearchResult }
  | { kind: "goto"; key: string; label: string; to: string };

export function SearchOverlay({
  onClose,
  embedded = false,
}: {
  onClose: () => void;
  embedded?: boolean;
}) {
  const navigate = useNavigate();
  const workItems = useStore((s) => Object.values(s.workItems));
  const repos = useStore((s) => uniq(Object.values(s.workItems).map((w) => w.repo)));
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [advanced, setAdvanced] = useState(false);
  const [sourceKind, setSourceKind] = useState("");
  const [kind, setKind] = useState("");
  const [repo, setRepo] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [gateError, setGateError] = useState<string | null>(null);
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [beads, setBeads] = useState<Bead[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!q.trim()) {
      setResults([]);
      setError(null);
      return;
    }
    const t = setTimeout(() => {
      api
        .search({ q, source_kind: sourceKind, kind, repo, mode })
        .then((r) => {
          setResults(r.results);
          setError(null);
        })
        .catch((e) => {
          setResults([]);
          setError(e instanceof Error ? e.message : String(e));
        });
    }, 250);
    return () => clearTimeout(t);
  }, [q, sourceKind, kind, repo, mode]);

  // Beads are live via the hub, not indexed — the one line in this overlay that
  // is not a lagging shadow (design 1h footer).
  useEffect(() => {
    if (!q.trim()) {
      setBeads([]);
      return;
    }
    const t = setTimeout(() => {
      api
        .searchBeads(q)
        .then((r) => setBeads(r.beads))
        .catch(() => setBeads([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  const openDocument = (r: SearchResult) => {
    const wid = r.links[0]?.work_item_id;
    if (wid) {
      // UI v2 · 05 (Item page group) has not necessarily merged yet, and
      // `WorkItemDetail.tsx`'s tab state is local, not router-read today —
      // this is a harmless no-op until that group wires
      // `location.state.tab`/`documentId` (Kraft-d2i5).
      navigate(`/work-items/${wid}`, { state: { tab: "documents", documentId: r.id } });
      onClose();
      return;
    }
    setOpenDoc(r.id);
  };

  // Gates first (design 09): the palette's own contextual-actions list.
  // `pending_gate` is the last gate_requested event, not scoped to
  // needs_human — a paused or abandoned item still carries one, so this
  // also requires status === "needs_human" to actually be actionable.
  const gated = workItems.filter((i) => i.pending_gate && i.status === "needs_human");
  const actionRows: Extract<Row, { kind: "action" }>[] = gated
    .filter((i) => !q.trim() || `approve ${i.pending_gate} ${i.title}`.toLowerCase().includes(q.toLowerCase()))
    .map((i) => ({
      kind: "action" as const,
      key: `action:${i.id}`,
      label: `Approve ${i.pending_gate}`,
      sub: i.title,
      act: () => {
        // human_review_approval carries a deferred-findings roll-up that
        // this palette has nowhere to show — Gate.tsx refuses to approve
        // it inline for the same reason. Route to the item instead.
        if (i.pending_gate === "human_review_approval") {
          navigate(`/work-items/${i.id}`);
          onClose();
          return;
        }
        api
          .approveGate(i.id, i.pending_gate as string)
          .then(onClose)
          .catch((e) => setGateError(e instanceof Error ? e.message : String(e)));
      },
    }));

  const workItemRows: Extract<Row, { kind: "workitem" }>[] = workItems
    .filter((i) => q.trim() && i.title.toLowerCase().includes(q.toLowerCase()))
    .slice(0, 5)
    .map((i) => ({
      kind: "workitem" as const,
      key: `wi:${i.id}`,
      label: i.title,
      sub: `${repoName(i.repo)} · ${i.chain_template} · ${i.status}`,
      to: `/work-items/${i.id}`,
    }));

  const documentRows: Extract<Row, { kind: "document" }>[] = results.map((r) => ({
    kind: "document" as const,
    key: `doc:${r.id}`,
    result: r,
  }));

  const gotoRows: Extract<Row, { kind: "goto" }>[] = GOTO_PAGES.filter(
    (p) => !q.trim() || p.label.toLowerCase().includes(q.toLowerCase()),
  ).map((p) => ({ kind: "goto" as const, key: `goto:${p.to}`, label: p.label, to: p.to }));

  const rows: Row[] = [...actionRows, ...workItemRows, ...documentRows, ...gotoRows];

  useEffect(() => {
    setActiveIndex(0);
  }, [q]);

  const activate = (row: Row | undefined) => {
    if (!row) return;
    if (row.kind === "action") row.act();
    else if (row.kind === "workitem" || row.kind === "goto") {
      navigate(row.to);
      onClose();
    } else if (row.kind === "document") openDocument(row.result);
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, rows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      activate(rows[activeIndex]);
    }
  };

  const documentsBody = useMemo(() => {
    if (error) return <p className="form-error">{error}</p>;
    if (!q.trim()) return <p className="search-hint">type to search specs, plans, summaries</p>;
    if (!results.length) return <p className="search-hint">no matches</p>;
    return null;
  }, [error, q, results]);

  const content = (
    <div className={embedded ? "search-page" : "dialog search-overlay elev-lg"}>
      <div className="search-bar">
        <MagnifyingGlass size={16} className="search-icon" />
        <input
          ref={inputRef}
          type="search"
          aria-label="search"
          placeholder="Search…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <span className="seg search-mode">
          {MODES.map((m) => (
            <label key={m} className="seg-opt">
              <input
                type="radio"
                name="search-mode"
                checked={mode === m}
                onChange={() => setMode(m)}
              />
              {m}
            </label>
          ))}
        </span>
        {!embedded && (
          <button type="button" className="btn btn-ghost search-esc" onClick={onClose}>
            esc
          </button>
        )}
      </div>

      <div className="search-facets">
        <span className="tag tag-neutral">source: {sourceKind || "any"}</span>
        <span className="tag tag-neutral">kind: {kind || "any"}</span>
        <span className="tag tag-neutral">repo: {repo || "any"}</span>
        <button
          type="button"
          className="btn btn-ghost search-advanced"
          aria-expanded={advanced}
          onClick={() => setAdvanced((v) => !v)}
        >
          advanced
        </button>
        <span className="search-count">
          {results.length} {results.length === 1 ? "result" : "results"} · lagging index, not
          live state
        </span>
      </div>

      {advanced && (
        <div className="search-filters">
          <label>
            source_kind
            <select
              aria-label="source_kind"
              value={sourceKind}
              onChange={(e) => setSourceKind(e.target.value)}
            >
              <option value="">any</option>
              <option value="artifact">artifact</option>
              <option value="session_summary">session_summary</option>
            </select>
          </label>
          <label>
            kind
            <input
              className="input"
              aria-label="kind"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            />
          </label>
          <label>
            repo
            <select aria-label="repo" value={repo} onChange={(e) => setRepo(e.target.value)}>
              <option value="">any repo</option>
              {repos.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
          </label>
        </div>
      )}

      <div className="search-sections">
        {actionRows.length > 0 && (
          <section className="search-section" data-section="actions">
            <SectionLabel>Actions</SectionLabel>
            {gateError && <p className="form-error">{gateError}</p>}
            {actionRows.map((row) => (
              <button
                key={row.key}
                className="search-row"
                data-active={rows[activeIndex] === row || undefined}
                onClick={() => activate(row)}
              >
                <Check size={14} />
                <span className="search-row-text">
                  <span className="search-row-title">{row.label}</span>
                  <span className="search-row-sub">{row.sub}</span>
                </span>
              </button>
            ))}
          </section>
        )}

        {workItemRows.length > 0 && (
          <section className="search-section" data-section="work-items">
            <SectionLabel>Work items</SectionLabel>
            {workItemRows.map((row) => (
              <button
                key={row.key}
                className="search-row"
                data-active={rows[activeIndex] === row || undefined}
                onClick={() => activate(row)}
              >
                <span className="search-row-text">
                  <span className="search-row-title">{row.label}</span>
                  <span className="search-row-sub">{row.sub}</span>
                </span>
              </button>
            ))}
          </section>
        )}

        <section className="search-section" data-section="documents">
          <SectionLabel>Documents</SectionLabel>
          {documentsBody}
          {documentRows.map((row) => {
            const r = row.result;
            return (
              <button
                key={row.key}
                className="search-result"
                data-active={rows[activeIndex] === row || undefined}
                onClick={() => activate(row)}
              >
                <span className="search-result-title">{r.title}</span>
                <span className="search-result-where">
                  <span className="search-result-kind">{r.kind ?? r.source_kind}</span>·
                  <span title={r.repo}>{repoName(r.repo)}</span>
                </span>
                <span className="search-result-snippet">
                  <Snippet text={r.snippet} />
                </span>
              </button>
            );
          })}
        </section>

        {gotoRows.length > 0 && (
          <section className="search-section" data-section="goto">
            <SectionLabel>Go to</SectionLabel>
            {gotoRows.map((row) => (
              <Link
                key={row.key}
                to={row.to}
                className="search-row"
                data-active={rows[activeIndex] === row || undefined}
                onClick={onClose}
              >
                <span className="search-row-text">
                  <span className="search-row-title">{row.label}</span>
                </span>
              </Link>
            ))}
          </section>
        )}
      </div>

      <div className="bead-strip">
        <CirclesThree size={15} className="bead-icon" />
        <span className="bead-label">Beads · live via hub</span>
        {beads.length === 0 ? (
          <span className="bead-hit">no bead matches</span>
        ) : (
          beads.slice(0, 2).map((b) => (
            <span key={b.id} className="bead-hit">
              <code>{b.id}</code> {b.title}
              {/* closed beads are searchable (Kraft-evm), so say which are */}
              {b.status === "closed" && <span className="tag tag-neutral">closed</span>}
            </span>
          ))
        )}
      </div>
    </div>
  );

  return (
    <>
      {embedded ? (
        content
      ) : (
        <div
          className="dialog-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Search"
          {...backdropProps(onClose)}
        >
          {content}
        </div>
      )}
      {openDoc && <DocumentModal id={openDoc} onClose={() => setOpenDoc(null)} onNavigate={onClose} />}
    </>
  );
}
