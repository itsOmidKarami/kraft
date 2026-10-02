import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import * as api from "../../api";
import { repoName } from "../../format";
import { useStore } from "../../store";
import type { WorkItem } from "../../types";
import { act } from "../item/actions";
import { openPane } from "../item/Workspace";
import { HeaderActions, HeaderTail } from "../shell/HeaderActions";
import { clearFirstRun, FirstRun, savedFirstRun } from "../shell/FirstRun";
import { Menu } from "../ui/Menu";
import { chainOf, groupOf, groupsOf, type GroupBy, type SortBy } from "./model";
import { useBoardPrefs } from "./prefs";
import { Row } from "./Row";
import type { RowAction } from "./rowText";
import { useBoardQuery } from "./url";
import { BulkBar } from "./BulkBar";
import { Composer } from "./Composer";
import { Peek, type PeekTab } from "./Peek";
import { useResizable, useWidth } from "../graph/useResizable";
import { useBulk } from "./bulk";
import "./board.css";

const GROUP_LABEL: Record<GroupBy, string> = { status: "Status", repo: "Repo", chain: "Chain" };
const SORT_LABEL: Record<SortBy, string> = { attention: "Needs attention", updated: "Recently updated", created: "Created", title: "Title" };

const isTextField = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));

type Load = { state: "loading" | "ok" } | { state: "error"; error: string };

/** The list, read again on mount (C.2): the store's own load ran at boot and
 *  may have failed. Offline (C.3) while that read failed or the event socket
 *  is reconnecting; it clears once the socket is open and a read succeeds. */
function useListLoad() {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const connection = useStore((s) => s.connection);
  const refresh = useCallback(() => {
    setLoad((l) => (l.state === "error" ? l : { state: "loading" }));
    useStore.getState().bootstrap().then(
      () => setLoad({ state: "ok" }),
      (e: Error) => setLoad({ state: "error", error: e.message }),
    );
  }, []);
  useEffect(refresh, [refresh]);
  const was = useRef(connection);
  useEffect(() => {
    if (connection === "open" && was.current === "reconnecting") refresh();
    was.current = connection;
  }, [connection, refresh]);
  return { load, refresh, offline: load.state === "error" || connection === "reconnecting" };
}

/** A minute's clock for the rows' ages and "retry in". */
function useNow() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);
  return now;
}

/** `/`: the board (W6). First-run while no repo is connected, decided once
 *  on load so connecting one mid-setup does not swap the page away. */
export function BoardPage() {
  const prefs = useBoardPrefs();
  const [query, setQuery] = useBoardQuery(prefs?.group_by);
  const navigate = useNavigate();
  const now = useNow();
  const [fresh, setFresh] = useState(false);
  const [allDone, setAllDone] = useState(false);
  const [archiveDays, setArchiveDays] = useState<number | null>(null);
  const [checked, setChecked] = useState<Set<string>>(() => new Set());
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const filterRef = useRef<HTMLInputElement>(null);
  const [peekTab, setPeekTab] = useState<PeekTab>("overview");
  const [budgetEdit, setBudgetEdit] = useState(false);
  const [body, bodyW] = useWidth();
  const size = useResizable("board", bodyW);
  const itemsById = useStore((s) => s.workItems);
  const items = useMemo(() => Object.values(itemsById).filter((i) => i.display_status !== "archived"), [itemsById]);
  const { load, refresh, offline } = useListLoad();
  // A bulk answer with failures: those items stay checked (D.6).
  const last = useBulk((s) => s.last);
  useEffect(() => {
    if (last) setChecked(new Set("results" in last ? last.results.filter((r) => !r.ok).map((r) => r.id) : last.ids));
  }, [last]);

  useEffect(() => {
    api.getRepos().then((r) => {
      // A wizard left part-way through comes back, until its last step is done
      // or skipped; one whose repo is no longer connected starts over.
      const saved = savedFirstRun();
      if (saved && !r.repos.some((x) => x.path === saved.path)) clearFirstRun();
      setFresh(r.repos.length === 0 || savedFirstRun() != null);
    }).catch(() => {});
    api.getPolicy().then((p) => setArchiveDays(p.archive?.after_days ?? null)).catch(() => {});
  }, []);

  const groups = useMemo(
    () =>
      groupsOf(items, {
        filter: { q: query.q, repo: query.repo, chain: query.chain },
        group: query.group,
        sort: query.sort,
        doneCap: allDone ? null : (prefs?.show_done ?? 5),
      }),
    [items, query.q, query.repo, query.chain, query.group, query.sort, allDone, prefs?.show_done],
  );
  const needsN = items.filter((i) => groupOf(i) === "needs").length;

  const open = useCallback((id: string, search = "") => navigate(`/work-items/${encodeURIComponent(id)}${search}`), [navigate]);
  const peek = useCallback((id: string, tab: PeekTab = "overview", budget = false) => {
    setQuery({ sel: id, new: false });
    setPeekTab(tab);
    setBudgetEdit(budget);
  }, [setQuery]);
  const select = useCallback((id: string) => (prefs?.open_in === "full" ? open(id) : peek(id)), [prefs?.open_in, open, peek]);
  const toggle = useCallback((id: string) => setChecked((c) => {
    const n = new Set(c);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    return n;
  }), []);
  const onAction = useCallback(async (item: WorkItem, a: RowAction) => {
    if (a.kind === "gate") {
      openPane();
      return open(item.id, `?sel=${encodeURIComponent(a.gate)}`);
    }
    if (a.kind === "peek") return peek(item.id, a.tab);
    const r = await act.resume(item.id);
    setRowErrors((e) => {
      const { [item.id]: _, ...rest } = e;
      return r.ok ? rest : { ...rest, [item.id]: r.error };
    });
  }, [open, peek]);

  // "/" focuses the filter; Escape clears the selection once menus have had it.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.defaultPrevented) return;
      if (e.key === "/" && !isTextField(e.target)) {
        e.preventDefault();
        filterRef.current?.focus();
      } else if (e.key === "Escape" && query.sel) setQuery({ sel: "" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [query.sel, setQuery]);

  // A press anywhere outside the peek closes it: the list, the header, the
  // sidebar. A row picks its own item, and a menu, popover, dialog or toast
  // belongs to whatever opened it. A press on a scrollbar is not a click.
  useEffect(() => {
    if (!query.sel) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target;
      if (!(t instanceof Element) || t.closest(".pane, .board-row, .popover, .dialog-backdrop, .toasts")) return;
      if (t instanceof HTMLElement && t.clientWidth > 0 && (e.offsetX > t.clientWidth || e.offsetY > t.clientHeight)) return;
      setQuery({ sel: "" });
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [query.sel, setQuery]);
  // The peek's own collapse button and Escape close it as an outside press
  // does, leaving no rail, and hand focus back to the item's row.
  const closePeek = useCallback(() => {
    const id = query.sel;
    setQuery({ sel: "" });
    requestAnimationFrame(() => [...document.querySelectorAll<HTMLElement>("[data-row]")].find((r) => r.dataset.row === id)?.querySelector<HTMLElement>(".board-row-main")?.focus());
  }, [query.sel, setQuery]);

  // ↑/↓ move between rows, across groups.
  const onListKey = (e: KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const rows = [...(e.currentTarget as HTMLElement).querySelectorAll<HTMLElement>(".board-row-main")];
    const at = rows.indexOf(document.activeElement as HTMLElement);
    if (at < 0) return;
    e.preventDefault();
    rows[Math.max(0, Math.min(rows.length - 1, at + (e.key === "ArrowDown" ? 1 : -1)))]?.focus();
  };

  // FirstRun's last step opens the composer, which lives on the board.
  if (fresh && !query.new) return <FirstRun onDone={() => setFresh(false)} />;

  const count = (f: (i: WorkItem) => boolean) => String(items.filter(f).length);
  const repos = [...new Set(items.map((i) => i.repo))].sort((a, b) => repoName(a).localeCompare(repoName(b)));
  const chains = [...new Set(items.map(chainOf))].sort();

  return (
    <div className="board-page">
      <h1 className="board-visually-hidden">Board</h1>
      <HeaderTail>
        <span className="board-crumb-sep" aria-hidden>›</span>
        <Menu
          label="Repo"
          triggerClass="board-crumb-menu"
          trigger={<>{query.repo ? repoName(query.repo) : "all repos"} <span aria-hidden className="board-caret">▾</span></>}
          items={[
            { label: "All repos", checked: !query.repo, hint: String(items.length), onSelect: () => setQuery({ repo: "" }) },
            ...repos.map((r) => ({ label: repoName(r), checked: query.repo === r, hint: count((i) => i.repo === r), onSelect: () => setQuery({ repo: r }) })),
          ]}
        />
      </HeaderTail>
      <HeaderActions>
        {needsN > 0 && load.state !== "loading" && <span className="board-tag is-warn">{needsN} NEED YOU</span>}
        {offline && <span className="board-tag is-bad">OFFLINE</span>}
        <button type="button" className="btn btn-primary" disabled={offline} onClick={() => setQuery({ new: true, sel: "" })}>+ New work item</button>
      </HeaderActions>

      {offline && (
        <div className="board-offline" role="alert">
          <span className="board-offline-mark" aria-hidden>!</span>
          <span className="board-offline-text">
            Could not load the board{load.state === "error" ? `: ${load.error.replace(/\.$/, "")}` : ": the live connection dropped"}. Showing what was loaded before; actions are off until it reconnects.
          </span>
          <button type="button" className="btn btn-danger" onClick={refresh}>Retry now</button>
        </div>
      )}
      <div className="board-filters">
        <label className="board-filter">
          <span className="board-visually-hidden">Filter</span>
          <input ref={filterRef} type="search" value={query.q} placeholder="Filter by title or id" onChange={(e) => setQuery({ q: e.target.value })} />
        </label>
        <Menu
          label="Chain"
          triggerClass={`board-menu-btn${query.chain ? " is-set" : ""}`}
          trigger={<>{query.chain ? `Chain · ${query.chain}` : "Chain"} <span aria-hidden className="board-caret">▾</span></>}
          items={[
            { label: "All chains", checked: !query.chain, hint: String(items.length), onSelect: () => setQuery({ chain: "" }) },
            ...chains.map((c) => ({ label: c, checked: query.chain === c, hint: count((i) => chainOf(i) === c), onSelect: () => setQuery({ chain: c }) })),
          ]}
        />
        <span className="board-filters-gap" />
        <Menu
          label="Group by"
          triggerClass="board-menu-btn"
          note="Needs you always comes first."
          trigger={<><span className="board-menu-key">Group</span> {GROUP_LABEL[query.group]} <span aria-hidden className="board-caret">▾</span></>}
          items={(Object.keys(GROUP_LABEL) as GroupBy[]).map((g) => ({ label: GROUP_LABEL[g], checked: query.group === g, onSelect: () => setQuery({ group: g }) }))}
        />
        <Menu
          label="Sort by"
          triggerClass="board-menu-btn"
          trigger={<><span className="board-menu-key">Sort</span> {SORT_LABEL[query.sort]} <span aria-hidden className="board-caret">▾</span></>}
          items={(Object.keys(SORT_LABEL) as SortBy[]).map((s) => ({ label: SORT_LABEL[s], checked: query.sort === s, onSelect: () => setQuery({ sort: s }) }))}
        />
      </div>

      <div className="board-body" ref={body}>
        {/* The peek overlays the list: the list keeps its width with it open. */}
        <div className="board-list" onKeyDown={onListKey}>
          <div className="board-list-inner">
            {query.new && <Composer repoFilter={query.repo} onClose={() => setQuery({ new: false })} onCreated={(id) => peek(id)} />}
            {load.state === "loading" && items.length === 0 ? <Skeleton /> : groups.map((g) => (
              <section key={g.key} className="board-group" aria-label={g.label}>
                <h2 className="board-group-head">
                  <span>{g.label}</span>
                  {g.key === "needs" && g.total > 0 && <span className="board-dot" aria-hidden />}
                  <span className="board-count">{g.total}</span>
                  {g.done && (
                    <span className="board-note">
                      <Link to="/archived">completed and cancelled{archiveDays != null ? ` · auto-archive after ${archiveDays} days` : ""}</Link>
                    </span>
                  )}
                  <span className="board-head-gap" />
                  {g.done && g.total > 0 && (
                    <button type="button" className="board-select-all" onClick={() => setChecked((c) => new Set([...c, ...g.rows.map((r) => r.id)]))}>Select all</button>
                  )}
                </h2>
                {g.rows.length === 0 && g.empty && <p className="board-empty">{g.empty}</p>}
                {g.rows.map((i) => (
                  <Row
                    key={i.id}
                    item={i}
                    now={now}
                    selected={query.sel === i.id}
                    checked={checked.has(i.id)}
                    offline={offline}
                    error={rowErrors[i.id]}
                    onSelect={select}
                    onOpen={open}
                    onCheck={toggle}
                    onAction={onAction}
                  />
                ))}
                {g.done && g.rows.length < g.total && (
                  <button type="button" className="board-more" onClick={() => setAllDone(true)}>show all {g.total}</button>
                )}
              </section>
            ))}
          </div>
        </div>
        {query.sel && (
          <Peek
            key={query.sel}
            id={query.sel}
            tab={peekTab}
            onTab={setPeekTab}
            budget={budgetEdit}
            onBudget={setBudgetEdit}
            offline={offline}
            size={size}
            onClose={closePeek}
            onRepo={(repo) => setQuery({ repo })}
          />
        )}
        <BulkBar checked={items.filter((i) => checked.has(i.id))} byId={itemsById} offline={offline} onChecked={(ids) => setChecked(new Set(ids))} />
      </div>
    </div>
  );
}

/** The board while its first list read runs (AreaBoard 65–70). */
function Skeleton() {
  return (
    <div className="board-skeleton" aria-busy="true" aria-label="Loading the board">
      {[3, 3, 2].map((rows, g) => (
        <div key={g}>
          <span className="sk sk-head" />
          {Array.from({ length: rows }, (_, r) => (
            <div key={r} className="sk-row">
              <span />
              <span className="sk sk-glyph" />
              <span className="sk-lines"><span className="sk" style={{ width: `${52 + ((g + r) % 3) * 12}%` }} /><span className="sk sk-short" /></span>
              <span className="sk sk-ticks" />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
