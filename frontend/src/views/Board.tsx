import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Prohibit } from "@phosphor-icons/react";
import { ChainBar } from "../components/ui";
import { Gate } from "../components/Gate";
import { ago } from "../format";
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
        >
          {name}
          <span className="facet-count">{n}</span>
        </button>
      ))}
    </div>
  );
}

const DONE_PREVIEW = 5;

export function Board() {
  const items = useStore((s) => Object.values(s.workItems));
  const connection = useStore((s) => s.connection);
  const [repo, setRepo] = useState<string | null>(null);
  const [tpl, setTpl] = useState<string | null>(null);
  const [allDone, setAllDone] = useState(false);

  useEffect(() => {
    useStore.getState().bootstrap().catch(() => {});
  }, []);

  // Each facet's counts are taken with the *other* facet applied, so the two
  // filters read as combining rather than as two independent views.
  const repoRows = useMemo(
    () => tally(tpl ? items.filter((i) => i.chain_template === tpl) : items, (i) => i.repo),
    [items, tpl],
  );
  const tplRows = useMemo(
    () => tally(repo ? items.filter((i) => i.repo === repo) : items, (i) => i.chain_template),
    [items, repo],
  );

  const shown = useMemo(
    () =>
      items.filter(
        (i) => (!repo || i.repo === repo) && (!tpl || i.chain_template === tpl),
      ),
    [items, repo, tpl],
  );

  const groups = [
    {
      id: "needs",
      label: "Needs you",
      tone: "accent" as const,
      // paused belongs here too: nothing moves until a person resumes it, and this
      // board is grouped by who is being waited on
      items: shown.filter((i) => i.status === "needs_human" || i.status === "paused"),
    },
    { id: "running", label: "Running", items: shown.filter((i) => i.status === "active") },
    { id: "done", label: "Done", items: shown.filter((i) => i.status === "completed") },
  ];

  return (
    <div className="board">
      <aside className="board-sidebar">
        <Facet label="Repos" rows={repoRows} value={repo} onPick={setRepo} />
        <Facet label="Template" rows={tplRows} value={tpl} onPick={setTpl} />
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
          <span>{item.repo}</span>
          <code>{item.id}</code>
          <span>{item.chain_template}</span>
          <span>{ago(item.updated_at)}</span>
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
