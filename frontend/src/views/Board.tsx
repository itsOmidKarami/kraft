import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { CaretDown, ClipboardText, Clock, Prohibit } from "@phosphor-icons/react";
import * as api from "../api";
import { Chip, MiniChain, OverflowMenu, StatusGlyph, TaskLine } from "../components/ui";
import { Gate } from "../components/Gate";
import { PeekPane } from "../components/PeekPane";
import { RepoSheet } from "../components/RepoSheet";
import { deriveState } from "../deriveState";
import { ago, clock, repoName } from "../format";
import { useStore } from "../store";
import { showToast } from "../components/Toast";
import type { Repo, Theme, WorkItem } from "../types";
import { usePhone } from "./work_item/usePhone";

const FILTERS_KEY = "kraft.board_filters";

/** Sidebar counts, sorted by name so the list does not reorder as work moves. */
function tally(items: WorkItem[], key: (i: WorkItem) => string) {
  const counts = new Map<string, number>();
  for (const i of items) counts.set(key(i), (counts.get(key(i)) ?? 0) + 1);
  return [...counts].sort(([a], [b]) => a.localeCompare(b));
}

/** Plain click: select only this row (replace), or clear if it was the only
 *  one already selected. Cmd/Ctrl-click: toggle this row into/out of the
 *  selection, leaving the rest alone — the Finder convention. */
function toggleFacet(current: Set<string>, name: string, additive: boolean): Set<string> {
  if (additive) {
    const next = new Set(current);
    next.has(name) ? next.delete(name) : next.add(name);
    return next;
  }
  return current.size === 1 && current.has(name) ? new Set<string>() : new Set([name]);
}

type FacetChip = { key: string; label: string; n: number; selected: boolean; pick: (additive: boolean) => void };

/** W4.1: at >= 768 the facet chips hold one row. Returns the index of the
 *  first chip that wrapped onto a second (hidden) line, or null when all fit
 *  -- measured, so a long repo name moves into "+N" whole instead of being
 *  cut to an ellipsis. The phone scrolls the row and never overflows. */
function useChipOverflow(ref: RefObject<HTMLDivElement>, enabled: boolean, key: string) {
  const [from, setFrom] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !enabled) {
      setFrom(null);
      return;
    }
    const measure = () => {
      const chips = [...el.children].filter((k) => k.classList.contains("chip")) as HTMLElement[];
      const i = chips.findIndex((c) => c.offsetTop > chips[0].offsetTop);
      setFrom(i === -1 ? null : i);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, enabled, key]);
  return from;
}

/** A status the poller drives forward on its own, not a person: not
 *  mid-agent-call, but not waiting on a human either. `rate_limited` (an API
 *  limit resetting) and `waiting` (a pipeline settling, Kraft-ru98) are two
 *  instances of the same shape, both carrying `retry_at` for when the poller
 *  next acts. Named once, used everywhere this file cares which statuses
 *  those are, so a third instance is one addition instead of two. */
function pollerDriven(i: WorkItem) {
  return i.status === "rate_limited" || i.status === "waiting";
}

const STATUS_GROUPS: { id: string; label: string; test: (i: WorkItem) => boolean }[] = [
  { id: "needs", label: "Needs you", test: (i) => deriveState(i).needsYou },
  {
    id: "running",
    label: "Running",
    // A rate-limited or waiting item is not mid-agent-call, but it is not
    // waiting on a person either -- the poller drives it forward on its own,
    // the same story "Running" already tells for an active item. Leaving
    // either out would drop it off the main view, which is "a slow run
    // looks like a hung one" from the other direction.
    test: (i) =>
      ["running", "rate_limited", "waiting"].includes(deriveState(i).state),
  },
  { id: "not_started", label: "Not started", test: (i) => deriveState(i).state === "not_started" },
  {
    id: "done",
    label: "Done",
    // Design 06: "Done N · completed and abandoned items" -- an abandoned
    // item is Done for the board's purposes even though `deriveState` keeps
    // its own distinct `abandoned` display state (its glyph/tone differ).
    test: (i) => ["done", "abandoned"].includes(deriveState(i).state),
  },
];

const SORTS: Record<string, (a: WorkItem, b: WorkItem) => number> = {
  updated: (a, b) => b.updated_at.localeCompare(a.updated_at),
  created: (a, b) => b.created_at.localeCompare(a.created_at),
  // Needs-you first, recently updated within each half (UI v3 · 04).
  attention: (a, b) =>
    Number(deriveState(b).needsYou) - Number(deriveState(a).needsYou) ||
    b.updated_at.localeCompare(a.updated_at),
  title: (a, b) => a.title.localeCompare(b.title),
};

const SORT_LABELS: Record<keyof typeof SORTS, string> = {
  updated: "recently updated",
  created: "created",
  attention: "needs attention",
  title: "title",
};

/** "Needs you" always leads regardless of axis — the one cross-cutting group
 *  the redesign's group-by control doesn't touch (design 34's "group by" is
 *  about the rest of the board, not about hiding what needs a person). */
function axisGroups(items: WorkItem[], axis: "repo" | "template", sort: keyof typeof SORTS) {
  const needsItems = items.filter((i) => deriveState(i).needsYou);
  const rest = items.filter((i) => !deriveState(i).needsYou);
  const key = axis === "repo" ? (i: WorkItem) => i.repo : (i: WorkItem) => i.chain_template;
  const byKey = new Map<string, WorkItem[]>();
  for (const i of rest) {
    const k = key(i);
    if (!byKey.has(k)) byKey.set(k, []);
    byKey.get(k)!.push(i);
  }
  const groups = [...byKey.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, its]) => ({
      id: k,
      label: axis === "repo" ? repoName(k) : k,
      tone: undefined as "accent" | undefined,
      items: its.sort(SORTS[sort]),
    }));
  if (needsItems.length === 0) return groups;
  return [
    { id: "needs", label: "Needs you", tone: "accent" as const, items: needsItems.sort(SORTS[sort]) },
    ...groups,
  ];
}

/** Persisted facet/sort state (design 04: "Chip + repo filter persist
 *  (localStorage/URL)") -- read once at mount, written on every change. A
 *  missing or unparsable key falls back to today's defaults rather than
 *  throwing. */
function loadFilters(): { repo: string[]; tpl: string[]; status: string[]; sort: string } {
  try {
    const raw = localStorage.getItem(FILTERS_KEY);
    if (!raw) throw new Error("no stored filters");
    const parsed = JSON.parse(raw);
    return {
      repo: Array.isArray(parsed.repo) ? parsed.repo : [],
      tpl: Array.isArray(parsed.tpl) ? parsed.tpl : [],
      status: Array.isArray(parsed.status) ? parsed.status : [],
      sort: typeof parsed.sort === "string" && parsed.sort in SORTS ? parsed.sort : "updated",
    };
  } catch {
    return { repo: [], tpl: [], status: [], sort: "updated" };
  }
}

export function Board({ onNewWorkItem }: { onNewWorkItem?: () => void } = {}) {
  // Archived items (poller, another tab, or the CLI can set archived_at at
  // any time over WS) are out of every board group, facet, and count until
  // restored -- the board is not the archive's read-only table (design 06).
  const items = useStore((s) => Object.values(s.workItems).filter((i) => !i.archived_at));
  const connection = useStore((s) => s.connection);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const peek = searchParams.get("peek");

  const [repos, setRepos] = useState<Repo[] | null>(null);
  useEffect(() => {
    // Leave `repos` null on failure, not []: [] renders FreshInstallBoard,
    // which hides the loadErr banner and reads as "connect a repo" instead
    // of "could not load the board".
    api.getRepos().then((r) => setRepos(r.repos)).catch(() => {});
  }, []);

  const [boardPrefs, setBoardPrefs] = useState<Theme["board"]>({
    group_by: "status",
    show_done: 5,
    open_in: "peek",
  });
  useEffect(() => {
    api.getTheme().then((t) => setBoardPrefs(t.board)).catch(() => {});
  }, []);

  const [archivedCount, setArchivedCount] = useState<number | null>(null);
  const refreshArchivedCount = () =>
    api.listArchivedWorkItems().then((r) => setArchivedCount(r.items.length)).catch(() => {});
  // Refetch on any work_item_archived/restored WS event too, not just our
  // own archive clicks -- the poller or another tab can archive/restore
  // items behind this one's back (Note 03: "counts update over WS").
  const archivedVersion = useStore((s) => s.archivedVersion);
  useEffect(() => {
    refreshArchivedCount();
  }, [archivedVersion]);

  // Done-group selection (design 06): only the Done group's rows get a
  // checkbox. Cleared on every archive so a stale id cannot post twice.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // W4.9: archiving says so, and can be taken back for 6s.
  const archiveIds = async (ids: string[]) => {
    await Promise.all(ids.map((id) => api.archiveWorkItem(id)));
    const refresh = () => useStore.getState().bootstrap().then(refreshArchivedCount);
    await refresh();
    showToast(`${ids.length} item${ids.length === 1 ? "" : "s"} archived`, undefined, {
      ms: 6000,
      action: { label: "Undo", run: () => Promise.all(ids.map((id) => api.restoreWorkItem(id))).then(refresh) },
    });
  };
  const archiveSelected = async () => {
    const ids = [...selected];
    setSelected(new Set());
    setPeek(null);
    await archiveIds(ids);
  };

  const initial = useMemo(loadFilters, []);
  // Empty set = no filter on that facet. Cmd/ctrl-click lets more than one
  // value be picked per facet (Facet's toggleFacet); facets still AND together.
  const [repo, setRepo] = useState<Set<string>>(new Set(initial.repo));
  const [tpl, setTpl] = useState<Set<string>>(new Set(initial.tpl));
  const [status, setStatus] = useState<Set<string>>(new Set(initial.status));
  const [sort, setSort] = useState<keyof typeof SORTS>(initial.sort as keyof typeof SORTS);
  const [allDone, setAllDone] = useState(false);

  useEffect(() => {
    localStorage.setItem(
      FILTERS_KEY,
      JSON.stringify({ repo: [...repo], tpl: [...tpl], status: [...status], sort }),
    );
  }, [repo, tpl, status, sort]);

  // A board that cannot reach the server rendered as a board with no work on
  // it — the same empty state as "you are all caught up". Say which it is.
  const [loadErr, setLoadErr] = useState<string | null>(null);
  useEffect(() => {
    useStore
      .getState()
      .bootstrap()
      .then(
        () => setLoadErr(null),
        (e) => setLoadErr(e instanceof Error ? e.message : String(e)),
      );
  }, []);

  // A facet with nothing picked matches everything; otherwise an item needs
  // only one of the picked values (OR within a facet, AND across facets).
  const matchesRepo = (i: WorkItem) => repo.size === 0 || repo.has(i.repo);
  const matchesTpl = (i: WorkItem) => tpl.size === 0 || tpl.has(i.chain_template);
  const matchesStatus = (i: WorkItem) =>
    status.size === 0 || STATUS_GROUPS.some((g) => status.has(g.label) && g.test(i));

  // Each facet's counts are taken with the *other two* applied, so all three
  // filters read as combining rather than as independent views.
  const repoRows = useMemo(
    () => tally(items.filter((i) => matchesTpl(i) && matchesStatus(i)), (i) => i.repo),
    [items, tpl, status],
  );
  const repoNeedsYou = useMemo(() => {
    const out: Record<string, number> = {};
    for (const i of items) if (deriveState(i).needsYou) out[i.repo] = (out[i.repo] ?? 0) + 1;
    return out;
  }, [items]);
  const [repoSheetOpen, setRepoSheetOpen] = useState(false);
  const chipsRef = useRef<HTMLDivElement>(null);
  const phone = usePhone();
  const tplRows = useMemo(
    () => tally(items.filter((i) => matchesRepo(i) && matchesStatus(i)), (i) => i.chain_template),
    [items, repo, status],
  );
  const statusRows = useMemo(() => {
    const base = items.filter((i) => matchesRepo(i) && matchesTpl(i));
    return STATUS_GROUPS.map((g) => [g.label, base.filter(g.test).length] as [string, number]);
  }, [items, repo, tpl]);

  const shown = useMemo(
    () => items.filter((i) => matchesRepo(i) && matchesTpl(i) && matchesStatus(i)),
    [items, repo, tpl, status],
  );

  // With one or more status facets picked, only their groups render — an
  // unpicked group would show as an empty "nothing here" section otherwise.
  const visibleStatusGroups =
    status.size === 0 ? STATUS_GROUPS : STATUS_GROUPS.filter((g) => status.has(g.label));

  const groups =
    boardPrefs.group_by === "status"
      ? visibleStatusGroups.map((g) => ({
          id: g.id,
          label: g.label,
          tone: g.id === "needs" ? ("accent" as const) : undefined,
          items: shown.filter(g.test).sort(SORTS[sort]),
        }))
      : axisGroups(shown, boardPrefs.group_by, sort);

  const setPeek = (id: string | null) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (id) next.set("peek", id);
        else next.delete("peek");
        return next;
      },
      { replace: true },
    );
  };

  const facetChips: FacetChip[] = [
    ...statusRows.map(([name, n]) => ({
      key: `status:${name}`, label: name, n, selected: status.has(name),
      pick: (a: boolean) => setStatus((cur) => toggleFacet(cur, name, a)),
    })),
    ...repoRows.map(([name, n]) => ({
      key: `repo:${name}`, label: repoName(name), n, selected: repo.has(name),
      pick: (a: boolean) => setRepo((cur) => toggleFacet(cur, name, a)),
    })),
    ...tplRows.map(([name, n]) => ({
      key: `tpl:${name}`, label: name, n, selected: tpl.has(name),
      pick: (a: boolean) => setTpl((cur) => toggleFacet(cur, name, a)),
    })),
  ];
  const overflowFrom = useChipOverflow(
    chipsRef,
    !phone,
    facetChips.map((c) => `${c.label}${c.n}`).join("|") + archivedCount,
  );
  const chipEl = (c: FacetChip, overflow: boolean) => (
    <Chip
      label={c.label}
      count={c.n}
      selected={c.selected}
      overflow={overflow}
      onClick={(e) => c.pick(e.metaKey || e.ctrlKey)}
    />
  );
  const archivedLink = (overflow: boolean) => (
    <Link
      to="/archived"
      className="chip"
      data-overflow={overflow || undefined}
      tabIndex={overflow ? -1 : undefined}
    >
      Archived <span className="chip-count">{archivedCount}</span>
    </Link>
  );

  if (repos !== null && repos.length === 0) {
    return <FreshInstallBoard onNewWorkItem={onNewWorkItem} />;
  }

  return (
    <div className={`board${peek ? " has-peek" : ""}`}>
      <div className="board-groups">
        {repoSheetOpen && (
          <RepoSheet
            repos={repoRows}
            needsYou={repoNeedsYou}
            value={repo}
            onPick={(name) => setRepo((cur) => toggleFacet(cur, name, false))}
            onClose={() => setRepoSheetOpen(false)}
          />
        )}
        <div className="board-filter-row" role="group" aria-label="filters">
          {/* One row: chips, then "+N" for what did not fit, then Sort (W4.1,
              W4.7). board-sort and board-more stay outside the chip box so
              their disclosures aren't clipped by its overflow. */}
          {/* Repo pill (design m01): phone's own way into the repo facet, on
              the facet row rather than a band of its own (W7/8) -- desktop
              reaches repos through the chips beside it. */}
          <button
            className="repo-pill phone-only"
            onClick={() => setRepoSheetOpen(true)}
          >
            <span className="repo-pill-avatar">{repoName(repo.size === 1 ? [...repo][0] : "all")[0]?.toUpperCase()}</span>
            {repo.size === 1 ? repoName([...repo][0]) : "all repos"}
            <CaretDown size={12} />
          </button>
          <div className="board-filter-chips" ref={chipsRef}>
            {facetChips.map((c, i) => (
              <Fragment key={c.key}>
                {i === statusRows.length && <span className="board-filter-divider" />}
                {chipEl(c, overflowFrom != null && i >= overflowFrom)}
              </Fragment>
            ))}
            {archivedCount != null && archivedLink(overflowFrom != null && facetChips.length >= overflowFrom)}
          </div>
          {overflowFrom != null && (
            <details className="board-more">
              <summary className="chip" aria-label="more filters">
                +{facetChips.length + (archivedCount != null ? 1 : 0) - overflowFrom}
              </summary>
              <div className="board-more-menu">
                {facetChips.slice(overflowFrom).map((c) => (
                  <Fragment key={c.key}>{chipEl(c, false)}</Fragment>
                ))}
                {archivedCount != null && archivedLink(false)}
              </div>
            </details>
          )}
          <details className="board-sort">
            <summary>
              Sort · {SORT_LABELS[sort]} <CaretDown size={11} />
            </summary>
            <div className="board-sort-menu">
              {Object.keys(SORTS).map((k) => (
                <button
                  key={k}
                  onClick={(e) => {
                    setSort(k as keyof typeof SORTS);
                    (e.currentTarget.closest("details") as HTMLDetailsElement).open = false;
                  }}
                >
                  {SORT_LABELS[k]}
                </button>
              ))}
            </div>
          </details>
        </div>
        {loadErr && (
          <p className="form-error" role="alert">
            could not load the board — {loadErr}
          </p>
        )}
        {groups.map((g) => (
          <section key={g.id}>
            <div className="group-head">
              <span className="group-label" data-tone={g.tone}>{g.label}</span>
              <span className="group-count">{g.items.length}</span>
              {g.id === "done" && g.items.length > 0 && (
                <button
                  className="btn btn-ghost"
                  onClick={() => setSelected(new Set(g.items.map((i) => i.id)))}
                >
                  Select all
                </button>
              )}
              {g.id === "done" && (
                <span className="group-head-note">
                  · completed and abandoned items · auto-archive after 30 days
                </span>
              )}
            </div>
            {g.items.length === 0 && (
              <div className="group-empty">nothing here for this filter</div>
            )}
            {(g.id === "done" && !allDone ? g.items.slice(0, boardPrefs.show_done) : g.items).map((i) => (
              <BoardRow
                key={i.id}
                item={i}
                selected={peek === i.id}
                selectable={g.id === "done"}
                checked={selected.has(i.id)}
                onCheck={(checked) =>
                  setSelected((cur) => {
                    const next = new Set(cur);
                    checked ? next.add(i.id) : next.delete(i.id);
                    return next;
                  })
                }
                onArchive={() => archiveIds([i.id])}
                onLongPress={() => setPeek(i.id)}
                onSelect={(metaKey) => {
                  if (metaKey || boardPrefs.open_in === "full") {
                    navigate(`/work-items/${i.id}`);
                    return;
                  }
                  setPeek(peek === i.id ? null : i.id);
                }}
              />
            ))}
            {g.id === "done" && !allDone && g.items.length > boardPrefs.show_done && (
              <button className="btn btn-ghost show-all" onClick={() => setAllDone(true)}>
                show all {g.items.length}
              </button>
            )}
          </section>
        ))}
        {selected.size > 0 && (
          <div className="board-floating-bar">
            <span>{`${selected.size} selected`}</span>
            <button
              className="btn btn-primary"
              data-testid="archive-selected"
              onClick={archiveSelected}
            >
              Archive
            </button>
          </div>
        )}
        <div className="board-foot">
          {/* the header's ConnBadge names the exact state; here it is just a pulse */}
          <span className="live" data-connection={connection} title={connection}>
            <span className="live-dot" />
            {connection === "open" ? "live" : "offline"}
          </span>
        </div>
      </div>
      {peek && <PeekPane id={peek} onClose={() => setPeek(null)} />}
    </div>
  );
}

/** ponytail: `window.innerWidth`, not a CSS class check -- the click
 *  target's *behavior* (peek vs. navigate) isn't something CSS can express,
 *  unlike every other phone/desktop split in this file (`.phone-only`/
 *  `.desktop-only`). Kept to this one handler. */
function isPhoneWidth(): boolean {
  return Boolean(window.matchMedia?.("(max-width: 767px)")?.matches);
}

const LONG_PRESS_MS = 500;

function BoardRow({
  item,
  selected,
  onSelect,
  onLongPress,
  selectable = false,
  checked = false,
  onCheck,
  onArchive,
}: {
  item: WorkItem;
  selected: boolean;
  onSelect: (metaKey: boolean) => void;
  /** Phone only (design m03): a long-press lifts the row and opens the peek
   *  sheet, instead of the plain-tap "open the item" default. */
  onLongPress?: () => void;
  /** Only the Done group's rows get a leading checkbox (design 06). */
  selectable?: boolean;
  checked?: boolean;
  onCheck?: (checked: boolean) => void;
  onArchive?: () => void;
}) {
  const navigate = useNavigate();
  const gate = item.status === "needs_human" ? (item.pending_gate ?? null) : null;
  const capped = item.status === "completed" ? null : item.cappedOut;
  const retryAt = pollerDriven(item) ? item.retry_at : null;
  const pressTimer = useRef<ReturnType<typeof setTimeout>>();
  const longPressed = useRef(false);
  const startPress = () => {
    longPressed.current = false;
    pressTimer.current = setTimeout(() => {
      longPressed.current = true;
      onLongPress?.();
    }, LONG_PRESS_MS);
  };
  const endPress = () => clearTimeout(pressTimer.current);
  return (
    <div
      className="board-row"
      data-testid="board-card"
      data-id={item.id}
      role="button"
      tabIndex={0}
      data-selected={selected || undefined}
      onPointerDown={startPress}
      onPointerUp={endPress}
      onPointerLeave={endPress}
      onClick={(e) => {
        if (longPressed.current) {
          longPressed.current = false;
          return;
        }
        if (e.metaKey || e.ctrlKey) {
          onSelect(true);
          return;
        }
        // Phone (design m01): a plain tap opens the item; toggling the peek
        // inline is desktop's own default (Task 8) -- the long-press sheet
        // above is the phone way to peek.
        if (isPhoneWidth()) {
          navigate(`/work-items/${item.id}`);
          return;
        }
        onSelect(false);
      }}
      onKeyDown={(e) => {
        // Only the row itself, not a bubbled Enter from a focused child
        // (the inline Escalate textarea, Approve/Archive buttons, the
        // checkbox) -- those should keep their own Enter behavior.
        if (e.key === "Enter" && e.target === e.currentTarget) {
          navigate(`/work-items/${item.id}`);
        }
      }}
    >
      {selectable ? (
        <input
          type="checkbox"
          checked={checked}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => onCheck?.(e.target.checked)}
        />
      ) : (
        <StatusGlyph status={deriveState(item).state} />
      )}
      <div className="board-row-main">
        {/* Not a link (UI v3 · G1-03): the whole row toggles the peek, and
            only the peek's "Open →" and a ⌘-click go to the full page. */}
        <span className="board-row-title" title={item.title}>
          {item.title}
        </span>
        <div className="board-row-meta">
          {/* Tablet width (768-1023) drops the node column (50); this is
              hidden by CSS everywhere else and only leads the line there. */}
          <span className="board-row-meta-current">{item.current_node_id}</span>
          <span title={item.repo}>{repoName(item.repo)}</span>
          {item.bead_id && <code>{item.bead_id}</code>}
          <span>{item.chain_template}</span>
          {/* Kraft-qqz8, board part: "Task N/M · title" when the implementation
              node has reported progress — spec §4 order puts this before ago,
              so the count (never dropped) outlives ago and the task title as
              the line clips. */}
          {item.progress && <TaskLine progress={item.progress} form="short" />}
          <span>{ago(item.updated_at)}</span>
          {/* Provenance, not status: the right-hand column is a fixed 120px and
              nowrap, so a chip there pushed the whole row past the viewport. */}
          {item.attachments?.length ? (
            <span className="tag tag-outline tag-tight">
              from {item.attachments.map((a) => a.kind).join("+")}
            </span>
          ) : null}
        </div>
        {gate && (
          <div onClick={(e) => e.stopPropagation()}>
            <Gate item={item} gate={gate} variant="inline" navigateReject />
          </div>
        )}
      </div>
      <MiniChain
        nodes={item.chain_definition.nodes}
        currentNodeId={item.current_node_id}
        done={item.completedNodes}
        size="sm"
        paused={["paused", "capped", "abandoned", "rate_limited", "waiting"].includes(
          deriveState(item).state,
        )}
      />
      <div className="board-row-current">
        {/* Tablet (768-1023) drops just this text -- it leads the meta line
            instead (.board-row-meta-current) -- but keeps the tags and the
            Archive/overflow buttons below, per spec §1 "buttons hide last". */}
        <span className="board-row-current-node">{item.current_node_id}</span>
        {item.fixCycle != null && (
          <span className="tag tag-outline tag-tight">fix·{item.fixCycle}</span>
        )}
        {capped && (
          <span className="tag tag-outline tag-tight">
            <Prohibit size={10} />
            capped {capped.cycles}/{capped.attempts}
          </span>
        )}
        {retryAt && (
          <span className="tag tag-outline tag-tight">
            <Clock size={10} />
            retry {clock(retryAt)}
          </span>
        )}
        {selectable && (
          <span className="board-row-archive" onClick={(e) => e.stopPropagation()}>
            <button className="btn btn-ghost" onClick={onArchive}>
              Archive
            </button>
            <OverflowMenu
              items={[
                { label: "Archive", onSelect: () => onArchive?.() },
                { label: "Copy id", onSelect: () => navigator.clipboard.writeText(item.id) },
              ]}
            />
          </span>
        )}
      </div>
    </div>
  );
}

/** Design 08. `onNewWorkItem` is the same callback `Header`'s "New work
 *  item" button fires — lifted through `App.tsx` since `Board` does not
 *  otherwise own that dialog's open state. */
function FreshInstallBoard({ onNewWorkItem }: { onNewWorkItem?: () => void }) {
  const [copied, setCopied] = useState(false);
  const copyCommand = async () => {
    try {
      await navigator.clipboard.writeText("kraft admin init");
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="board-empty">
      <h1>Nothing on the board yet</h1>
      <p>
        Kraft is running at <code>127.0.0.1:8765</code> with the default chain and
        policy. Connect a repo and file the first work item; it is created paused, so
        nothing runs until you start it.
      </p>
      <ol className="board-empty-steps">
        <li>
          <div className="board-empty-step-text">
            <strong>Connect a repo</strong>
            <span>a local git checkout · Kraft probes .gitmodules, .beads/, the test command and the forge remote</span>
          </div>
          <Link className="btn btn-primary" to="/settings/repos">
            + Add repo
          </Link>
        </li>
        <li>
          <div className="board-empty-step-text">
            <strong>Check the chain and policy</strong>
            <span>default · 11 nodes, 4 gates · 3 fix attempts, $10 per item</span>
          </div>
          <Link to="/settings/chains">Chains</Link>
        </li>
        <li>
          <div className="board-empty-step-text">
            <strong>File a work item</strong>
            <span>from a title, or from an existing spec or plan</span>
          </div>
          <button className="btn btn-primary" onClick={onNewWorkItem}>
            + New work item
          </button>
        </li>
        <li>
          <div className="board-empty-step-text">
            <strong>Or drive it from an agent session</strong>
            <span>
              <code>kraft admin init</code> registers the MCP server and the /kraft:* skills
            </span>
          </div>
          <button className="btn btn-secondary" onClick={copyCommand}>
            <ClipboardText size={14} />
            {copied ? "Copied" : "Copy command"}
          </button>
        </li>
      </ol>
      <p className="board-empty-footer">
        Everything Settings writes is YAML in ~/.kraft/templates — diff it, revert it, git init it.
      </p>
    </div>
  );
}
