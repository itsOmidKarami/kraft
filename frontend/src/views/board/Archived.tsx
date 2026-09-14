import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Archive, CaretDown } from "@phosphor-icons/react";
import * as api from "../../api";
import { ShortId } from "../../components/ShortId";
import { ago, repoName } from "../../format";
import type { WorkItem } from "../../types";

const SHOW_PREVIEW = 5;
const SORT_LABELS = { archived: "archived date", title: "title (A–Z)" };

/**
 * The Archived view (UI v2 · 03, design 07): a read-only table of everything
 * `archive_work_item` has touched. Client-side search/sort over the one
 * fetch -- "search archive" is a view control on data already in hand, not a
 * second backend query.
 */
export function ArchivedView() {
  const [items, setItems] = useState<WorkItem[] | null>(null);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"archived" | "title">("archived");
  const [showAll, setShowAll] = useState(false);

  const load = () => api.listArchivedWorkItems().then((r) => setItems(r.items));
  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(() => {
    const rows = (items ?? []).filter((i) => i.title.toLowerCase().includes(q.toLowerCase()));
    return [...rows].sort((a, b) =>
      sort === "title"
        ? a.title.localeCompare(b.title)
        : (b.archived_at ?? "").localeCompare(a.archived_at ?? ""),
    );
  }, [items, q, sort]);

  const restore = async (id: string) => {
    await api.restoreWorkItem(id);
    await load();
  };

  return (
    <div className="archived">
      <div className="board-filter-row" role="group" aria-label="filters">
        <span className="chip" aria-pressed="true">
          Archived <span className="chip-count">{items?.length ?? 0}</span>
        </span>
        <input
          className="input archived-search"
          type="search"
          aria-label="search archive"
          placeholder="search archive"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {/* The board's own sort disclosure, not a native <select> (W4.7). */}
        <details className="board-sort">
          <summary>
            Sort · {SORT_LABELS[sort]} <CaretDown size={11} />
          </summary>
          <div className="board-sort-menu">
            {(Object.keys(SORT_LABELS) as (keyof typeof SORT_LABELS)[]).map((k) => (
              <button
                key={k}
                onClick={(e) => {
                  setSort(k);
                  (e.currentTarget.closest("details") as HTMLDetailsElement).open = false;
                }}
              >
                {SORT_LABELS[k]}
              </button>
            ))}
          </div>
        </details>
      </div>

      <p className="archived-banner">
        Archived items are read-only and out of every board count. Worktrees are reclaimed;
        documents, logs and the timeline stay, and search still finds them. <strong>Restore</strong>{" "}
        puts an item back under Done.
      </p>

      {/* An empty table is one line, not a header row over nothing (W4.7). */}
      {items !== null && filtered.length === 0 ? (
        <p className="empty">{q ? "no archived item matches" : "nothing here yet"}</p>
      ) : (
      <div className="archived-table">
        <div className="archived-head">
          <span>WORK ITEM</span>
          <span>ENDED AS</span>
          <span>ARCHIVED</span>
          <span />
        </div>
        {(showAll ? filtered : filtered.slice(0, SHOW_PREVIEW)).map((item) => (
          <Link key={item.id} to={`/work-items/${item.id}`} className="archived-row">
            <span className="archived-row-item">
              <Archive size={16} />
              <span className="archived-row-text">
                <span className="archived-row-title">{item.title}</span>
                <span className="archived-row-meta">
                  <span title={item.repo}>{repoName(item.repo)}</span>
                  <ShortId id={item.id} />
                </span>
              </span>
            </span>
            <span>{item.status}</span>
            <span>
              {ago(item.archived_at)} · {item.archived_by === "auto" ? "auto" : "by you"}
            </span>
            <span className="archived-row-actions">
              <button
                className="btn btn-ghost"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  restore(item.id);
                }}
              >
                ↺ Restore
              </button>
            </span>
          </Link>
        ))}
      </div>
      )}
      {!showAll && filtered.length > SHOW_PREVIEW && (
        <button className="btn btn-ghost show-all" onClick={() => setShowAll(true)}>
          show all {filtered.length}
        </button>
      )}
    </div>
  );
}
