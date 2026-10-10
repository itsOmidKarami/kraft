import { Fragment, useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Box, CircleDot, Diamond, FileText, Plus, Search, type LucideIcon } from "lucide-react";
import * as api from "../../api";
import { docTitle, repoName, shortId } from "../../format";
import { useStore } from "../../store";
import type { Bead, SearchResult, WorkItem } from "../../types";
import { backdropProps, useModal } from "../../useModal";
import { groupOf } from "../board/model";
import { termsOf } from "../item/DocViewer";
import { gateWords, reasonTail } from "../board/rowText";
import { headerState, neverStarted } from "../item/status";
import { Kbd } from "../ui/Kbd";
import { Tabs } from "../ui/Tabs";
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
  /** Leads the sub and is the one part of it that shortens: a gate row's item title. */
  subLead?: string;
  snippet?: string;
  /** What Enter does, in the footer. */
  note: string;
  /** Its kind's icon: an item a box, a gate to review a diamond, a document a page, a bead a circle-dot, a page its nav icon. */
  icon: LucideIcon;
  /** A short status after the title (a document's kind, a bead's status). */
  tag?: string;
  /** Where it lives: the repo, or the sidebar group of a page. */
  where?: string;
  open: () => void;
}

/** The board's composer (W6 brief A.2). */
const NEW_ITEM = { path: "/?new=1", label: "New work item", icon: Plus, group: null };
const has = (hay: string, q: string) => hay.toLowerCase().includes(q.toLowerCase());
const GROUP = { templates: "Templates", settings: "Settings" } as const;
const SECTIONS: Record<Row["section"], string> = { needs: "Needs you", items: "Work items", docs: "Documents", beads: "Beads", goto: "Go to" };

/** FTS brackets the matched terms: [like] this. */
function Snippet({ text }: { text: string }) {
  return <>{text.split(/(\[[^\]]*\])/).map((p, i) => (p.startsWith("[") && p.endsWith("]") ? <mark key={i}>{p.slice(1, -1)}</mark> : <Fragment key={i}>{p}</Fragment>))}</>;
}

/** The query's terms marked in a title, as the snippet marks what FTS matched. */
function Marked({ text, query }: { text: string; query: string }) {
  const re = termsOf(query);
  if (!re) return <>{text}</>;
  // A capturing split: every odd part is a match.
  return <>{text.split(new RegExp(`(${re.source})`, "gi")).map((p, i) => (i % 2 ? <mark key={i}>{p}</mark> : <Fragment key={i}>{p}</Fragment>))}</>;
}

/** What the board's row says of an item, not its stored status: "not started", "approve spec", "failed at plan". */
const stateWords = (i: WorkItem) => (neverStarted(i) ? "not started" : reasonTail(i));

const itemRow = (i: WorkItem, section: "needs" | "items", go: (to: string) => void): Row =>
  // An item waiting at a gate is an action: review that gate.
  section === "needs" && i.pending_gate
    ? {
        id: `${section}:${i.id}`,
        section,
        // The gate as the board words it: "Review spec", not "Review spec_approval" (#502 review).
        label: `Review ${gateWords(i.pending_gate)}`,
        subLead: i.title,
        sub: [shortId(i.id), repoName(i.repo)].filter(Boolean).join(" · "),
        icon: Diamond,
        tag: headerState(i).badge,
        note: `reviews ${i.title}`,
        open: () => go(`/work-items/${encodeURIComponent(i.id)}/review`),
      }
    : {
        id: `${section}:${i.id}`,
        section,
        label: i.title,
        sub: stateWords(i),
        icon: Box,
        tag: headerState(i).badge,
        where: repoName(i.repo),
        note: "opens the work item",
        open: () => go(`/work-items/${encodeURIComponent(i.id)}`),
      };

/** The ⌘K palette. A document of a work item opens on that item's page (`?doc=&q=`),
 *  one with no item in `onDocument`'s dialog, and a bead starts a draft item that implements it. */
export function SearchOverlay({ onClose, onDocument }: { onClose: () => void; onDocument: (id: string, query: string) => void }) {
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
    // Needs you is the board's group: a gate, a question, a stop, a failure, an item paused mid-chain.
    const needsYou = all.filter((i) => groupOf(i) === "needs" && (!query || has(i.title, query) || has(i.id, query)));
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
        const wid = r.links.find((l) => l.work_item_id)?.work_item_id;
        return {
          id: `docs:${r.id}`,
          section: "docs",
          label: docTitle({ ...r, ...r.links[0], content: r.snippet.replace(/[[\]]/g, "") }),
          icon: FileText,
          tag: r.kind ?? r.source_kind,
          where: repoName(r.repo),
          snippet: r.snippet,
          note: wid ? "opens the document on its work item" : "opens the document",
          // The query goes along, so the viewer can find it in the text.
          open: () => (wid ? navigate(`/work-items/${encodeURIComponent(wid)}?${new URLSearchParams({ doc: r.id, q: query })}`) : onDocument(r.id, query)),
        };
      }),
      ...beads.list
        .filter((b) => !withBead.has(b.id))
        .map((b): Row => ({
          id: `beads:${b.id}`,
          section: "beads",
          label: b.title,
          sub: b.id,
          icon: CircleDot,
          tag: b.status ?? undefined,
          note: "drafts a work item that implements this bead",
          open: () => navigate(`/work-items/new?${new URLSearchParams({ title: b.title, bead: b.id })}`),
        })),
      ...[...ROUTES, NEW_ITEM].filter((r) => !query || has(r.label, query)).map((r): Row => ({
        id: `goto:${r.path}`,
        section: "goto",
        label: r.label,
        icon: r.icon,
        where: r.group === "templates" || r.group === "settings" ? GROUP[r.group] : undefined,
        note: `goes to ${r.label}`,
        open: () => navigate(r.path),
      })),
    ];
    return out;
  }, [workItems, docs.results, beads.list, query, navigate, onDocument]);

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
                  <r.icon size={14} aria-hidden className={`ng-search-ico${r.icon === Diamond ? " is-gate" : ""}`} />
                  <span className="ng-search-main">
                    <span className="ng-search-title" title={r.label}><Marked text={r.label} query={query} /></span>
                    {r.sub && (
                      <span className="ng-search-sub">
                        {r.subLead && <><span className="ng-search-sublead" data-allow-ellipsis title={r.subLead}><Marked text={r.subLead} query={query} /></span><span aria-hidden>&nbsp;·&nbsp;</span></>}
                        <span className="ng-search-subrest">{r.sub}</span>
                      </span>
                    )}
                    {r.snippet && <span className="ng-search-snippet"><Snippet text={r.snippet} /></span>}
                  </span>
                  {r.tag && <span className="ng-search-tag">{r.tag}</span>}
                  {r.where && <span className="ng-search-where">{r.where}</span>}
                  {r === row && <span className="ng-search-key" aria-hidden>⏎</span>}
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
  const filtersOn = [sourceKind, kind.trim()].filter(Boolean).length;

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
        <div className="ng-search-tabrow">
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
          <button type="button" className="ng-search-filter" aria-expanded={filters} aria-label={filtersOn ? `Filters, ${filtersOn} on` : "Filters"} onClick={() => setFilters((f) => !f)}>
            Filters{filtersOn > 0 && <span className="ng-search-filter-n" aria-hidden>{filtersOn}</span>}
          </button>
        </div>
        {(filters || filtersOn > 0) && (
          <div className="ng-search-filters">
            <label className="ng-search-fchip"><span>source</span><select value={sourceKind} onChange={(e) => setSourceKind(e.target.value)}><option value="">any</option><option value="artifact">artifact</option><option value="session_summary">session summary</option></select></label>
            <label className="ng-search-fchip"><span>kind</span><input value={kind} placeholder="any" size={8} onChange={(e) => setKind(e.target.value)} /></label>
            <span className="ng-search-fnote">Filters narrow Documents only</span>
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
