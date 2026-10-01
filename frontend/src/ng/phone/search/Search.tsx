import { CircleDot, FileText, Kanban, MoveRight, Search as SearchIcon } from "lucide-react";
import { useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { docTitle, repoName } from "../../../format";
import { useStore } from "../../../store";
import type { Bead, SearchResult, WorkItem } from "../../../types";
import { ROUTES } from "../../shell/routes";
import { Doc } from "../doc/Doc";
import { ChoiceSheet, EditSheet, useSheet } from "../nav/Sheet";
import { RootHeader } from "../nav/ScreenHeader";
import { useSearch, type Filters, type Load } from "./useSearch";
import "./search.css";

const has = (hay: string, q: string) => hay.toLowerCase().includes(q.toLowerCase());
type Section = "needs" | "items" | "docs" | "beads" | "goto";
const HEAD: Record<Section, string> = { needs: "Needs you", items: "Work items", docs: "Documents", beads: "Beads", goto: "Go to" };
const ICON: Record<Section, typeof Kanban> = { needs: Kanban, items: Kanban, docs: FileText, beads: CircleDot, goto: MoveRight };

interface Row {
  id: string;
  section: Section;
  title: string;
  sub?: string;
  open: () => void;
}

const SOURCES = [{ value: "", label: "Any source" }, { value: "artifact", label: "Documents" }, { value: "session_summary", label: "Session summaries" }];
const MODES = [{ value: "hybrid", label: "Hybrid" }, { value: "fts", label: "Keywords" }, { value: "vector", label: "Meaning" }];

/** `/search` (W17 brief I): items, documents and beads in the sections the ⌘K overlay has, in one list. A Needs you row opens the gate and never approves. Only what the server returned is shown. */
export function Search() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const sheet = useSheet();
  const byId = useStore((s) => s.workItems);
  const q = params.get("q") ?? "";
  const query = q.trim();
  const f: Filters = { source: params.get("source") ?? "", kind: params.get("kind") ?? "", repo: params.get("repo") ?? "", mode: params.get("mode") ?? "hybrid" };
  const { docs, beads } = useSearch(query, f);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k));
    setParams(next, { replace: true });
  };
  const items = useMemo(() => Object.values(byId).filter((i) => i.display_status !== "archived"), [byId]);

  const rows = useMemo(() => {
    const needs = (i: WorkItem) => i.display_status === "needs_you" && i.stop?.kind === "gate" && !!i.pending_gate;
    const match = (i: WorkItem) => !query || has(i.title, query) || has(i.id, query) || has(i.bead_id ?? "", query) || has(i.repo, query);
    const itemRow = (i: WorkItem, section: "needs" | "items"): Row => ({
      id: `${section}:${i.id}`,
      section,
      title: i.title,
      sub: [repoName(i.repo), section === "needs" ? i.pending_gate : i.display_status?.replace("_", " ")].filter(Boolean).join(" · "),
      // Opens the gate: the decision is made on the review screen, never from a search result (GAP §2 #27).
      open: () => navigate(section === "needs" ? `/work-items/${encodeURIComponent(i.id)}/review?gate=${encodeURIComponent(i.pending_gate!)}` : `/work-items/${encodeURIComponent(i.id)}`),
    });
    const hits = items.filter(match);
    const needsRows = hits.filter(needs).map((i) => itemRow(i, "needs"));
    const rest = hits.filter((i) => !needs(i)).sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, query ? 8 : 8);
    const withBead = new Set(items.map((i) => i.bead_id).filter(Boolean));
    return [
      ...needsRows,
      ...rest.map((i) => itemRow(i, "items")),
      ...docs.results.map((r: SearchResult): Row => ({
        id: `docs:${r.id}`,
        section: "docs",
        title: docTitle({ ...r, ...r.links[0], content: r.snippet.replace(/[[\]]/g, "") }),
        sub: `${r.kind ?? r.source_kind} · ${repoName(r.repo)}`,
        open: () => { const n = new URLSearchParams(params); n.set("doc", r.id); navigate(`?${n}`); },
      })),
      ...beads.list.filter((b: Bead) => !withBead.has(b.id)).map((b): Row => ({
        id: `beads:${b.id}`,
        section: "beads",
        title: b.title,
        sub: [b.id, b.status].filter(Boolean).join(" · "),
        open: () => navigate(`/work-items/new?title=${encodeURIComponent(b.title)}`),
      })),
      ...(query ? [...ROUTES, { path: "/work-items/new", label: "New work item" }].filter((r) => has(r.label, query)).map((r): Row => ({ id: `goto:${r.path}`, section: "goto", title: r.label, open: () => navigate(r.path) })) : []),
    ];
  }, [items, query, docs.results, beads.list, navigate, params]);

  if (params.get("doc")) return <Doc id={params.get("doc")!} />;

  const sections = (["needs", "items", "docs", "beads", "goto"] as Section[]).map((s) => ({ s, list: rows.filter((r) => r.section === s) }));
  const failed = (what: string, load: Load) => load === "error" && <p className="ph-note ph-error" role="status">{what} could not be searched.</p>;
  const nothing = query && !rows.length && docs.load !== "loading" && beads.load !== "loading" && docs.load !== "error";
  const chip = (id: string, label: string, on: boolean) => (
    <button key={id} type="button" className={`ph-chip${on ? " ph-is-on" : ""}`} onClick={() => sheet.open(id)}>{label}</button>
  );

  return (
    <>
      <RootHeader title="Search" />
      <div className="ph-search-field">
        <SearchIcon size={17} aria-hidden="true" />
        <input type="search" className="ph-search-input" aria-label="Search" placeholder="Items, documents, beads" value={q} onChange={(e) => set({ q: e.target.value })} />
      </div>
      <div className="ph-chips" role="group" aria-label="Filters">
        {chip("source", SOURCES.find((x) => x.value === f.source)?.label ?? "Any source", !!f.source)}
        {chip("kind", f.kind ? `Kind: ${f.kind}` : "Any kind", !!f.kind)}
        {chip("repo", f.repo ? repoName(f.repo) : "All repos", !!f.repo)}
        {chip("mode", MODES.find((x) => x.value === f.mode)?.label ?? "Hybrid", f.mode !== "hybrid")}
      </div>
      <div className="ph-content ph-search-results">
        {sections.map(({ s, list }) => list.length > 0 && (
          <section key={s} aria-label={s === "items" && !query ? "Recent" : HEAD[s]} className="ph-search-section">
            <h2 className="ph-search-head">{s === "items" && !query ? "Recent" : HEAD[s]}</h2>
            {list.map((r) => {
              const Icon = ICON[s];
              return (
                <button key={r.id} type="button" className="ph-search-row" onClick={r.open}>
                  <span className="ph-search-icon"><Icon size={16} aria-hidden="true" /></span>
                  <span className="ph-row-text"><span className="ph-row-label">{r.title}</span>{r.sub && <span className="ph-row-hint">{r.sub}</span>}</span>
                </button>
              );
            })}
          </section>
        ))}
        {docs.load === "loading" && !docs.results.length && <p className="ph-note">Searching documents…</p>}
        {failed("Documents", docs.load)}
        {failed("Beads", beads.load)}
        {docs.load === "ok" && docs.results.length > 0 && <p className="ph-note">Documents come from a lagging index, not live state.</p>}
        {nothing && <p className="ph-empty">Nothing matches.</p>}
      </div>
      {sheet.is("source") && <ChoiceSheet title="Search in" value={f.source} options={SOURCES} onPick={(v) => sheet.closeThen(() => set({ source: v }))} onClose={sheet.close} />}
      {sheet.is("mode") && <ChoiceSheet title="Match by" value={f.mode} options={MODES} onPick={(v) => sheet.closeThen(() => set({ mode: v === "hybrid" ? "" : v }))} onClose={sheet.close} />}
      {sheet.is("repo") && (
        <ChoiceSheet
          title="Repo"
          value={f.repo}
          options={[{ value: "", label: "All repos" }, ...[...new Set(items.map((i) => i.repo))].sort().map((r) => ({ value: r, label: repoName(r) }))]}
          onPick={(v) => sheet.closeThen(() => set({ repo: v }))}
          onClose={sheet.close}
        />
      )}
      {sheet.is("kind") && <EditSheet title="Kind" text="Narrow documents to one kind, such as spec, plan or review. Leave it empty for any." initial={f.kind} placeholder="spec" submitLabel="Set" onSubmit={(v) => sheet.closeThen(() => set({ kind: v.trim() }))} onClose={sheet.close} />}
    </>
  );
}
