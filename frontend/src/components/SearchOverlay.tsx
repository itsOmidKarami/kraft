import { useEffect, useMemo, useRef, useState } from "react";
import { CirclesThree, MagnifyingGlass } from "@phosphor-icons/react";
import * as api from "../api";
import { repoName } from "../format";
import { backdropProps } from "../useModal";
import { useStore } from "../store";
import type { Bead, SearchResult } from "../types";
import { DocumentModal } from "./DocumentModal";
import { Snippet } from "./Snippet";

/**
 * Search over indexed artifacts (design 1h). The results are a lagging shadow
 * of the repo, never live state — the header says so, and no row renders a
 * status (spec §7).
 */

const uniq = (xs: string[]) => [...new Set(xs)].sort();
const MODES = ["hybrid", "fts", "vector"];

export function SearchOverlay({ onClose }: { onClose: () => void }) {
  const repos = useStore((s) => uniq(Object.values(s.workItems).map((w) => w.repo)));
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [advanced, setAdvanced] = useState(false);
  const [sourceKind, setSourceKind] = useState("");
  const [kind, setKind] = useState("");
  const [repo, setRepo] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [beads, setBeads] = useState<Bead[]>([]);
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

  const body = useMemo(() => {
    if (error) return <p className="form-error">{error}</p>;
    if (!q.trim()) return <p className="search-hint">type to search specs, plans, summaries</p>;
    if (!results.length) return <p className="search-hint">no matches</p>;
    return (
      <div className="search-results">
        {results.map((r) => (
          <button key={r.id} className="search-result" onClick={() => setOpenDoc(r.id)}>
            <span className="search-result-title">{r.title}</span>
            <span className="search-result-where">
              <span className="search-result-kind">{r.kind ?? r.source_kind}</span>·
              <span title={r.repo}>{repoName(r.repo)}</span>
            </span>
            <span className="search-result-snippet">
              <Snippet text={r.snippet} />
            </span>
          </button>
        ))}
      </div>
    );
  }, [error, q, results]);

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="Search" {...backdropProps(onClose)}>
      <div className="dialog search-overlay elev-lg">
        <div className="search-bar">
          <MagnifyingGlass size={16} className="search-icon" />
          <input
            ref={inputRef}
            type="search"
            aria-label="search"
            placeholder="Search…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
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
          <button type="button" className="btn btn-ghost search-esc" onClick={onClose}>
            esc
          </button>
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

        {body}

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
      {openDoc && (
        <DocumentModal id={openDoc} onClose={() => setOpenDoc(null)} onNavigate={onClose} />
      )}
    </div>
  );
}
