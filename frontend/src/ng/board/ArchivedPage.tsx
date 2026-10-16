import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import * as api from "../../api";
import { ago } from "../../format";
import { useStore } from "../../store";
import type { WorkItem } from "../../types";
import { act } from "../item/actions";
import { Menu } from "../ui/Menu";
import { NoMatch } from "../ui/NoMatch";
import { archivedLede, ARCHIVED_READ_ERROR, NOTHING_ARCHIVED } from "../ui/archivedWords";
import { BulkBar } from "./BulkBar";
import { sendBulk } from "./bulk";
import { Row } from "./Row";
import "./board.css";
import "../item/item.css";
import { LinkButton } from "../ui/LinkButton";
import { Empty } from "../ui/Empty";

type Sort = "archived" | "created" | "title";
const SORT_LABEL: Record<Sort, string> = { archived: "Recently archived", created: "Created", title: "Title" };
const desc = (a?: string | null, b?: string | null) => ((a ?? "") < (b ?? "") ? 1 : (a ?? "") > (b ?? "") ? -1 : 0);

/** `/archived` (R5, GAP §2 #26): archived items with Restore, from the
 *  Done group's auto-archive line and ⌘K. A row opens the item page. */
export function ArchivedPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const sort = (params.get("sort") as Sort) in SORT_LABEL ? (params.get("sort") as Sort) : "archived";
  const set = (k: string, v: string) => setParams((p) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); return n; }, { replace: true });
  const [items, setItems] = useState<WorkItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [checked, setChecked] = useState<Set<string>>(() => new Set());
  const [archiveDays, setArchiveDays] = useState<number | null>(null);
  useEffect(() => {
    api.getPolicy().then((p) => setArchiveDays(p.archive?.after_days ?? null)).catch(() => {});
  }, []);
  const version = useStore((s) => s.archivedVersion);
  const now = Date.now();

  const load = useCallback(() => {
    api.listArchivedWorkItems().then((r) => { setItems(r.items); setError(null); }, (e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load, version]);

  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    const list = (items ?? []).filter((i) => !t || [i.title, i.id, i.bead_id ?? "", i.repo].some((s) => s.toLowerCase().includes(t)));
    const by = { archived: (a: WorkItem, b: WorkItem) => desc(a.archived_at, b.archived_at), created: (a: WorkItem, b: WorkItem) => desc(a.created_at, b.created_at), title: (a: WorkItem, b: WorkItem) => a.title.localeCompare(b.title) }[sort];
    return list.sort(by);
  }, [items, q, sort]);

  const restore = async (id: string) => {
    const r = await act.restore(id);
    setRowErrors((e) => { const { [id]: _, ...rest } = e; return r.ok ? rest : { ...rest, [id]: r.error }; });
    if (r.ok) load();
  };
  const toggle = (id: string) => setChecked((c) => { const n = new Set(c); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const restoreChecked = async () => {
    const ids = [...checked];
    setChecked(new Set());
    const out = await sendBulk("restore", ids);
    if ("results" in out) setChecked(new Set(out.results.filter((r) => !r.ok).map((r) => r.id)));
    load();
  };

  return (
    <div className="board-page">
      <header className="board-page-head">
        <h1>Archived</h1>
        <p>{archivedLede(archiveDays)}</p>
      </header>
      <div className="board-filters">
        <label className="board-filter">
          <span className="board-visually-hidden">Filter</span>
          <input type="search" value={q} placeholder="Filter by title or id" onChange={(e) => set("q", e.target.value)} />
        </label>
        <span className="board-filters-gap" />
        <Menu
          label="Sort by"
          triggerClass="board-menu-btn"
          trigger={<><span className="board-menu-key">Sort</span> {SORT_LABEL[sort]} <span aria-hidden className="board-caret">▾</span></>}
          items={(Object.keys(SORT_LABEL) as Sort[]).map((s) => ({ label: SORT_LABEL[s], checked: sort === s, onSelect: () => set("sort", s === "archived" ? "" : s) }))}
        />
      </div>
      <div className="board-body">
        <div className="board-list">
          <div className="board-list-inner">
            {error && <p className="item-error board-empty board-empty-solo" role="alert">{ARCHIVED_READ_ERROR(error)}</p>}
            {items && !shown.length && (q.trim() ? <NoMatch className="board-empty board-empty-solo" query={q} onClear={() => set("q", "")} /> : <Empty className="board-empty board-empty-solo">{NOTHING_ARCHIVED}</Empty>)}
            {shown.length > 0 && (
              <section className="board-group" aria-label="Archived">
                <h2 className="board-group-head"><span>Archived</span><span className="board-count">{shown.length}</span></h2>
                {shown.map((i) => (
                  <Row
                    key={i.id}
                    item={i}
                    now={now}
                    selected={false}
                    checked={checked.has(i.id)}
                    offline={false}
                    error={rowErrors[i.id]}
                    onSelect={(id) => navigate(`/work-items/${encodeURIComponent(id)}`)}
                    onOpen={(id) => navigate(`/work-items/${encodeURIComponent(id)}`)}
                    onCheck={toggle}
                    onAction={() => {}}
                    own={{ label: "Restore", run: () => void restore(i.id), tail: `archived by ${i.archived_by ?? "you"}`, age: ago(i.archived_at, now) }}
                  />
                ))}
              </section>
            )}
          </div>
        </div>
        {checked.size > 0 && (
          <div className="bulk-wrap">
            <div className="bulk-bar" role="toolbar" aria-label="Selected work items">
              <span>{checked.size} selected</span>
              <button type="button" className="btn btn-primary" onClick={() => void restoreChecked()}>Restore {checked.size}</button>
              <LinkButton onClick={() => setChecked(new Set())}>Clear</LinkButton>
            </div>
          </div>
        )}
        {checked.size === 0 && <BulkBar checked={[]} byId={Object.fromEntries((items ?? []).map((i) => [i.id, i]))} offline={false} onChecked={(ids) => setChecked(new Set(ids))} />}
      </div>
    </div>
  );
}
