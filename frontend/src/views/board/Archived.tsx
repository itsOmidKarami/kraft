import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Archive } from "@phosphor-icons/react";
import * as api from "../../api";
import { ago, repoName } from "../../format";
import type { WorkItem } from "../../types";

const SHOW_PREVIEW = 5;

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
          className="input"
          type="search"
          aria-label="search archive"
          placeholder="search archive"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="board-sort">
          <label htmlFor="archived-sort">Sort</label>
          <select
            id="archived-sort"
            value={sort}
            onChange={(e) => setSort(e.target.value as "archived" | "title")}
          >
            <option value="archived">archived date</option>
            <option value="title">title (A–Z)</option>
          </select>
        </div>
      </div>

      <p className="archived-banner">
        Archived items are read-only and out of every board count. Worktrees are reclaimed;
        documents, logs and the timeline stay, and search still finds them. <strong>Restore</strong>{" "}
        puts an item back under Done.
      </p>

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
                  <code>{item.id}</code>
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
      {!showAll && filtered.length > SHOW_PREVIEW && (
        <button className="btn btn-ghost show-all" onClick={() => setShowAll(true)}>
          show all {filtered.length}
        </button>
      )}
    </div>
  );
}
