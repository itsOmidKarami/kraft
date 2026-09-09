import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Prohibit } from "@phosphor-icons/react";
import { ChainBar } from "../components/ui";
import { Gate } from "../components/Gate";
import { ago, repoName } from "../format";
import { useStore } from "../store";
import type { WorkItem } from "../types";

/** Sidebar counts, sorted by name so the list does not reorder as work moves. */
function tally(items: WorkItem[], key: (i: WorkItem) => string) {
  const counts = new Map<string, number>();
  for (const i of items) counts.set(key(i), (counts.get(key(i)) ?? 0) + 1);
  return [...counts].sort(([a], [b]) => a.localeCompare(b));
}

function Facet({
  label,
  rows,
  value,
  onPick,
}: {
  label: string;
  rows: [string, number][];
  value: string | null;
  onPick: (v: string | null) => void;
}) {
  return (
    <div className="facet">
      <div className="section-label">{label}</div>
      {rows.map(([name, n]) => (
        <button
          key={name}
          className="facet-opt"
          aria-pressed={name === value}
          // clicking the selected facet clears it — there is no explicit "all" row
          onClick={() => onPick(name === value ? null : name)}
          title={name}
        >
          {label === "Repos" ? repoName(name) : name}
          <span className="facet-count">{n}</span>
        </button>
      ))}
    </div>
  );
}

const DONE_PREVIEW = 5;

/** A created-but-never-resumed item: `paused` with no current node (api.py:527-529).
 *  Distinct from a mid-chain pause, which is still `paused` but has run at least
 *  one node — that one is waiting on the same person as `needs_human`, so it groups
 *  with "Needs you" instead. */
function notStarted(i: WorkItem) {
  return i.status === "paused" && i.current_node_id === null;
}

function needsYou(i: WorkItem) {
  return i.status === "needs_human" || (i.status === "paused" && !notStarted(i));
}

const STATUS_GROUPS: { id: string; label: string; test: (i: WorkItem) => boolean }[] = [
  { id: "needs", label: "Needs you", test: needsYou },
  { id: "running", label: "Running", test: (i) => i.status === "active" },
  { id: "not_started", label: "Not started", test: notStarted },
  { id: "done", label: "Done", test: (i) => i.status === "completed" },
];

const SORTS: Record<string, (a: WorkItem, b: WorkItem) => number> = {
  updated: (a, b) => b.updated_at.localeCompare(a.updated_at),
  title: (a, b) => a.title.localeCompare(b.title),
  repo: (a, b) => a.repo.localeCompare(b.repo),
};

export function Board() {
  const items = useStore((s) => Object.values(s.workItems));
  const connection = useStore((s) => s.connection);
  const [repo, setRepo] = useState<string | null>(null);
  const [tpl, setTpl] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [sort, setSort] = useState<keyof typeof SORTS>("updated");
  const [allDone, setAllDone] = useState(false);

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

  const matchesStatus = (i: WorkItem, label: string | null) =>
    !label || (STATUS_GROUPS.find((g) => g.label === label)?.test(i) ?? true);

  // Each facet's counts are taken with the *other two* applied, so all three
  // filters read as combining rather than as independent views.
  const repoRows = useMemo(
    () =>
      tally(
        items.filter((i) => (!tpl || i.chain_template === tpl) && matchesStatus(i, status)),
        (i) => i.repo,
      ),
    [items, tpl, status],
  );
  const tplRows = useMemo(
    () =>
      tally(
        items.filter((i) => (!repo || i.repo === repo) && matchesStatus(i, status)),
        (i) => i.chain_template,
      ),
    [items, repo, status],
  );
  const statusRows = useMemo(() => {
    const base = items.filter(
      (i) => (!repo || i.repo === repo) && (!tpl || i.chain_template === tpl),
    );
    return STATUS_GROUPS.map((g) => [g.label, base.filter(g.test).length] as [string, number]);
  }, [items, repo, tpl]);

  const shown = useMemo(
    () =>
      items.filter(
        (i) =>
          (!repo || i.repo === repo) &&
          (!tpl || i.chain_template === tpl) &&
          matchesStatus(i, status),
      ),
    [items, repo, tpl, status],
  );

  const groups = STATUS_GROUPS.map((g) => ({
    id: g.id,
    label: g.label,
    tone: g.id === "needs" ? ("accent" as const) : undefined,
    items: shown.filter(g.test).sort(SORTS[sort]),
  }));

  return (
    <div className="board">
      <aside className="board-sidebar">
        <Facet label="Repos" rows={repoRows} value={repo} onPick={setRepo} />
        <Facet label="Template" rows={tplRows} value={tpl} onPick={setTpl} />
        <Facet label="Status" rows={statusRows} value={status} onPick={setStatus} />
        <div className="board-foot">
          {/* the header's ConnBadge names the exact state; here it is just a pulse */}
          <span className="live" data-connection={connection} title={connection}>
            <span className="live-dot" />
            {connection === "open" ? "live" : "offline"}
          </span>
          {/* Design gap: the design's "index rescan: 2 min ago" has no source —
              nothing reports when the indexer last ran. Omitted until it does. */}
        </div>
      </aside>

      <div className="board-groups">
        <div className="board-sort">
          <label htmlFor="board-sort">Sort by</label>
          <select
            id="board-sort"
            value={sort}
            onChange={(e) => setSort(e.target.value as keyof typeof SORTS)}
          >
            <option value="updated">Recently updated</option>
            <option value="title">Title (A–Z)</option>
            <option value="repo">Repo</option>
          </select>
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
            </div>
            {g.items.length === 0 && (
              <div className="group-empty">nothing here for this filter</div>
            )}
            {(g.id === "done" && !allDone ? g.items.slice(0, DONE_PREVIEW) : g.items).map((i) => (
              <BoardRow key={i.id} item={i} />
            ))}
            {g.id === "done" && !allDone && g.items.length > DONE_PREVIEW && (
              <button className="btn btn-ghost show-all" onClick={() => setAllDone(true)}>
                show all {g.items.length}
              </button>
            )}
          </section>
        ))}
      </div>
    </div>
  );
}

function BoardRow({ item }: { item: WorkItem }) {
  const gate = item.status === "needs_human" ? (item.pending_gate ?? null) : null;
  const capped = item.status === "completed" ? null : item.cappedOut;
  return (
    <div className="board-row" data-testid="board-card">
      <div className="board-row-main">
        <Link className="board-row-title" to={`/work-items/${item.id}`}>
          {item.title}
        </Link>
        <div className="board-row-meta">
          <span title={item.repo}>{repoName(item.repo)}</span>
          {item.bead_id && <code>{item.bead_id}</code>}
          <span>{item.chain_template}</span>
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
          <Gate item={item} gate={gate} variant="inline" />
        )}
      </div>
      <ChainBar item={item} size="sm" />
      <div className="board-row-current">
        {item.current_node_id}
        {item.fixCycle != null && (
          <span className="tag tag-outline tag-tight">fix·{item.fixCycle}</span>
        )}
        {capped && (
          <span className="tag tag-outline tag-tight">
            <Prohibit size={10} />
            capped {capped.cycles}/{capped.attempts}
          </span>
        )}
      </div>
    </div>
  );
}
