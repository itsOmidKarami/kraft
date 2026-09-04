import { useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import { useStore } from "../store";
import type { SearchResult } from "../types";
import { DocumentModal } from "./DocumentModal";
import { Snippet } from "./Snippet";

const uniq = (xs: string[]) => [...new Set(xs)].sort();

export function SearchOverlay({ onClose }: { onClose: () => void }) {
  const repos = useStore((s) => uniq(Object.values(s.workItems).map((w) => w.repo)));
  const [q, setQ] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [sourceKind, setSourceKind] = useState("");
  const [kind, setKind] = useState("");
  const [repo, setRepo] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [openDoc, setOpenDoc] = useState<string | null>(null);
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
        .search({ q, source_kind: sourceKind, kind, repo })
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
  }, [q, sourceKind, kind, repo]);

  const body = useMemo(() => {
    if (error) return <p className="form-error">{error}</p>;
    if (!q.trim()) return <p className="search-hint">type to search specs, plans, summaries</p>;
    if (!results.length) return <p className="search-hint">no matches</p>;
    return (
      <ul className="search-results">
        {results.map((r) => (
          <li key={r.id}>
            <button className="search-result" onClick={() => setOpenDoc(r.id)}>
              <span className="search-result-title">{r.title}</span>
              <span className="chip" data-kind={r.kind ?? ""}>
                {r.kind ?? r.source_kind}
              </span>
              <span className="repo-tag">{r.repo}</span>
              <span className="search-result-snippet">
                <Snippet text={r.snippet} />
              </span>
            </button>
          </li>
        ))}
      </ul>
    );
  }, [error, q, results]);

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Search">
      <div className="modal search-overlay">
        <div className="search-bar">
          <input
            ref={inputRef}
            type="search"
            aria-label="search"
            placeholder="Search…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <button type="button" onClick={() => setAdvanced((v) => !v)}>
            advanced
          </button>
          <button type="button" onClick={onClose}>
            close
          </button>
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
              <input aria-label="kind" value={kind} onChange={(e) => setKind(e.target.value)} />
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
      </div>
      {openDoc && <DocumentModal id={openDoc} onClose={() => setOpenDoc(null)} />}
    </div>
  );
}
