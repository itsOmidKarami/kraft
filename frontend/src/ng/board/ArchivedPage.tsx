import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import * as api from "../../api";
import { ago } from "../../format";
import { useStore } from "../../store";
import type { WorkItem } from "../../types";
import { act } from "../item/actions";
import { Menu } from "../ui/Menu";
import { BulkBar } from "./BulkBar";
import { sendBulk } from "./bulk";
import { Row } from "./Row";
import "./board.css";
import "../item/item.css";

type Sort = "archived" | "created" | "title";
const SORT_LABEL: Record<Sort, string> = { archived: "Recently archived", created: "Created", title: "Title" };
const desc = (a?: string | null, b?: string | null) => ((a ?? "") < (b ?? "") ? 1 : (a ?? "") > (b ?? "") ? -1 : 0);

/** `/ng/archived` (R5, GAP §2 #26): archived items with Restore, from the
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
      <h1 className="board-visually-hidden">Archived</h1>
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
            {error && <p className="item-error board-empty" role="alert">{error}</p>}
            <section className="board-group" aria-label="Archived">
              <h2 className="board-group-head"><span>Archived</span><span className="board-count">{shown.length}</span></h2>
              {items && !shown.length && <p className="board-empty">{q.trim() ? "nothing here for this filter" : "Nothing is archived."}</p>}
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
          </div>
        </div>
        {checked.size > 0 && (
          <div className="bulk-wrap">
            <div className="bulk-bar" role="toolbar" aria-label="Selected work items">
              <span>{checked.size} selected</span>
              <button type="button" className="btn btn-primary" onClick={() => void restoreChecked()}>Restore {checked.size}</button>
              <button type="button" className="bulk-clear" onClick={() => setChecked(new Set())}>Clear</button>
            </div>
          </div>
        )}
        {checked.size === 0 && <BulkBar checked={[]} byId={Object.fromEntries((items ?? []).map((i) => [i.id, i]))} offline={false} onChecked={(ids) => setChecked(new Set(ids))} />}
      </div>
    </div>
  );
}
