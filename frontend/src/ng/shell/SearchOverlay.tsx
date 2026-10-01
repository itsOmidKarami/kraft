import { Fragment, useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Search } from "lucide-react";
import * as api from "../../api";
import { docTitle, repoName } from "../../format";
import { useStore } from "../../store";
import type { Bead, SearchResult, WorkItem } from "../../types";
import { backdropProps, useModal } from "../../useModal";
import { Kbd } from "../ui/Kbd";
import { Tabs } from "../ui/Tabs";
import { openShipped } from "./nav";
import { ROUTES } from "./routes";
import "./search.css";

type Tab = "all" | "items" | "docs" | "beads";
type Load = "idle" | "loading" | "ok" | "error";
const MODES = ["hybrid", "fts", "vector"] as const;

interface Row {
  id: string;
  section: "needs" | "items" | "docs" | "beads" | "goto";
  label: string;
  sub?: string;
  snippet?: string;
  /** What Enter does, in the footer. */
  note: string;
  open: () => void;
}

/** The board's composer (W6 brief A.2). */
const NEW_ITEM = { path: "/?new=1", label: "New work item" };
const has = (hay: string, q: string) => hay.toLowerCase().includes(q.toLowerCase());
const SECTIONS: Record<Row["section"], string> = { needs: "Needs you", items: "Work items", docs: "Documents", beads: "Beads", goto: "Go to" };

/** FTS brackets the matched terms: [like] this. */
function Snippet({ text }: { text: string }) {
  return <>{text.split(/(\[[^\]]*\])/).map((p, i) => (p.startsWith("[") && p.endsWith("]") ? <mark key={i}>{p.slice(1, -1)}</mark> : <Fragment key={i}>{p}</Fragment>))}</>;
}

const itemRow = (i: WorkItem, section: "needs" | "items", go: (to: string) => void): Row => ({
  id: `${section}:${i.id}`,
  section,
  label: i.title,
  sub: [repoName(i.repo), section === "needs" ? i.pending_gate : i.status].filter(Boolean).join(" · "),
  note: "opens the work item",
  open: () => go(`/work-items/${encodeURIComponent(i.id)}`),
});

/** The ⌘K palette. Items and Go to rows stay inside /ng; documents and beads
 *  open on the shipped UI until the pages they belong to are built. */
export function SearchOverlay({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const ref = useModal<HTMLDivElement>(onClose);
  const uid = useId();
  const workItems = useStore((s) => s.workItems);
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<Tab>("all");
  const [mode, setMode] = useState<(typeof MODES)[number]>("hybrid");
  const [filters, setFilters] = useState(false);
  const [sourceKind, setSourceKind] = useState("");
  const [kind, setKind] = useState("");
  const [docs, setDocs] = useState<{ load: Load; results: SearchResult[] }>({ load: "idle", results: [] });
  const [beads, setBeads] = useState<{ load: Load; list: Bead[] }>({ load: "idle", list: [] });
  const [active, setActive] = useState(0);
  const query = q.trim();
  const seq = useRef(0);

  useEffect(() => {
    if (!query) {
      seq.current++;
      setDocs({ load: "idle", results: [] });
      setBeads({ load: "idle", list: [] });
      return;
    }
    const mine = ++seq.current;
    setDocs((d) => ({ ...d, load: "loading" }));
    setBeads((b) => ({ ...b, load: "loading" }));
    const t = setTimeout(() => {
      api
        .search({ q: query, mode, source_kind: sourceKind, kind, limit: 8 })
        .then((r) => mine === seq.current && setDocs({ load: "ok", results: r.results.slice(0, 8) }))
        .catch(() => mine === seq.current && setDocs({ load: "error", results: [] }));
      api
        .searchBeads(query)
        .then((r) => mine === seq.current && setBeads({ load: "ok", list: r.beads }))
        .catch(() => mine === seq.current && setBeads({ load: "error", list: [] }));
    }, 200);
    return () => clearTimeout(t);
  }, [query, mode, sourceKind, kind]);

  const rows = useMemo(() => {
    const all = Object.values(workItems);
    const needsYou = all.filter((i) => i.pending_gate && i.status === "needs_human" && (!query || has(i.title, query) || has(i.id, query)));
    const taken = new Set(needsYou.map((i) => i.id));
    const rest = all.filter((i) => !taken.has(i.id));
    const items = query
      ? rest.filter((i) => has(i.title, query) || has(i.id, query)).slice(0, 5)
      : [...rest].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 2);
    const withBead = new Set(all.map((i) => i.bead_id).filter(Boolean));
    const out: Row[] = [
      ...needsYou.map((i) => itemRow(i, "needs", navigate)),
      ...items.map((i) => itemRow(i, "items", navigate)),
      ...docs.results.map((r): Row => {
        const wid = r.links[0]?.work_item_id;
        return {
          id: `docs:${r.id}`,
          section: "docs",
          label: docTitle({ ...r, ...r.links[0], content: r.snippet.replace(/[[\]]/g, "") }),
          sub: `${r.kind ?? r.source_kind} · ${repoName(r.repo)}`,
          snippet: r.snippet,
          note: "opens the document on the current UI",
          open: () => openShipped(wid ? `/work-items/${wid}` : "/search"),
        };
      }),
      ...beads.list
        .filter((b) => !withBead.has(b.id))
        .map((b): Row => ({
          id: `beads:${b.id}`,
          section: "beads",
          label: b.title,
          sub: [b.id, b.status].filter(Boolean).join(" · "),
          note: "starts a work item for this bead on the current UI",
          open: () => openShipped(`/?q=${encodeURIComponent(b.id)}`),
        })),
      ...[...ROUTES, NEW_ITEM].filter((r) => !query || has(r.label, query)).map((r): Row => ({
        id: `goto:${r.path}`,
        section: "goto",
        label: r.label,
        note: `goes to ${r.label}`,
        open: () => navigate(r.path),
      })),
    ];
    return out;
  }, [workItems, docs.results, beads.list, query, navigate]);

  const count = (s: Row["section"][]) => rows.filter((r) => s.includes(r.section)).length;
  const shown = rows.filter((r) => ({ all: true, items: r.section === "needs" || r.section === "items", docs: r.section === "docs", beads: r.section === "beads" })[tab] ?? false);
  const at = Math.max(0, Math.min(active, shown.length - 1));
  const row = shown[at];
  const optId = (r: Row) => `${uid}-opt-${r.id}`;

  useEffect(() => setActive(0), [q, tab]);
  useEffect(() => {
    if (row) document.getElementById(optId(row))?.scrollIntoView?.({ block: "nearest" });
  });

  const choose = useCallback((r: Row | undefined) => {
    if (!r) return;
    onClose();
    r.open();
  }, [onClose]);

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(Math.min(at + 1, shown.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(Math.max(at - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); choose(row); }
  };

  const failed = (label: string) => <p className="ng-search-note ng-search-bad" role="none">{label} could not be searched</p>;
  const sections = (["needs", "items", "docs", "beads", "goto"] as const).map((s) => {
    const list = shown.filter((r) => r.section === s);
    const noteHere = s === "docs" ? docs.load === "error" : s === "beads" ? beads.load === "error" : false;
    const wanted = s === "docs" ? tab === "all" || tab === "docs" : s === "beads" ? tab === "all" || tab === "beads" : true;
    if (!wanted || (!list.length && !noteHere && !(s === "docs" && docs.load === "loading"))) return null;
    const head = s === "items" && !query ? "Recent" : SECTIONS[s];
    return (
      <div key={s} role="group" aria-label={head} className="ng-search-group" data-section={s}>
        <div className="ng-search-head" aria-hidden="true">{head}</div>
        <div className={s === "goto" && !query ? "ng-search-chips" : undefined} role="none">
          {list.map((r) => (
            <div
              key={r.id}
              id={optId(r)}
              role="option"
              aria-selected={r === row}
              className={s === "goto" && !query ? "ng-search-chip" : "ng-search-row"}
              onMouseMove={() => setActive(shown.indexOf(r))}
              onClick={() => choose(r)}
            >
              {s === "goto" && !query ? r.label : (
                <>
                  <span className="ng-search-title" title={r.label}>{r.label}</span>
                  {r.sub && <span className="ng-search-sub">{r.sub}</span>}
                  {r.snippet && <span className="ng-search-snippet"><Snippet text={r.snippet} /></span>}
                </>
              )}
            </div>
          ))}
        </div>
        {s === "docs" && docs.load === "loading" && !list.length && <p className="ng-search-note" role="none">Searching documents…</p>}
        {s === "docs" && (noteHere ? failed("Documents") : list.length > 0 && <p className="ng-search-note" role="none">documents come from a lagging index, not live state</p>)}
        {s === "beads" && noteHere && failed("Beads")}
      </div>
    );
  });
  const empty = query && docs.load !== "loading" && beads.load !== "loading" && !shown.length && docs.load !== "error" && beads.load !== "error";
  const tabLabel = (l: string, n: number) => (query ? `${l} ${n}` : l);

  return (
    <div className="dialog-backdrop ng-search-backdrop" {...backdropProps(onClose)}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label="Search" className="ng-search">
        <div className="ng-search-bar">
          <Search size={16} aria-hidden="true" />
          <input
            data-autofocus
            type="text"
            role="combobox"
            aria-label="Search"
            aria-expanded="true"
            aria-controls={`${uid}-panel`}
            aria-activedescendant={row ? optId(row) : undefined}
            placeholder="Search work items, documents, beads, pages"
            autoComplete="off"
            spellCheck={false}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button type="button" className="ng-search-mode" aria-label={`Search mode: ${mode}. Change mode`} onClick={() => setMode(MODES[(MODES.indexOf(mode) + 1) % MODES.length])}>{mode}</button>
          <Kbd>esc</Kbd>
        </div>
        <Tabs
          id={uid}
          label="Search scope"
          value={tab}
          onChange={setTab}
          tabs={[
            { value: "all", label: tabLabel("All", count(["needs", "items", "docs", "beads"])) },
            { value: "items", label: tabLabel("Items", count(["needs", "items"])) },
            { value: "docs", label: tabLabel("Documents", count(["docs"])) },
            { value: "beads", label: tabLabel("Beads", count(["beads"])) },
          ]}
        />
        {query && (
          <div className="ng-search-filters">
            <button type="button" aria-expanded={filters} onClick={() => setFilters((f) => !f)}>Filters</button>
            <span>Filters narrow Documents only</span>
            {filters && (
              <>
                <label>Source<select value={sourceKind} onChange={(e) => setSourceKind(e.target.value)}><option value="">any</option><option value="artifact">artifact</option><option value="session_summary">session summary</option></select></label>
                <label>Kind<input value={kind} onChange={(e) => setKind(e.target.value)} /></label>
              </>
            )}
          </div>
        )}
        <div role="listbox" id={`${uid}-panel`} aria-labelledby={`${uid}-tab-${tab}`} className="ng-search-list">
          {sections}
          {empty && <p className="ng-search-note" role="none">No matches for “{query}”</p>}
        </div>
        <div className="ng-search-foot">
          <span><Kbd>↑</Kbd> <Kbd>↓</Kbd> move</span>
          <span><Kbd>⏎</Kbd> open</span>
          <span><Kbd>esc</Kbd> close</span>
          {row && <span className="ng-search-enter">⏎ {row.note}</span>}
        </div>
      </div>
    </div>
  );
}
