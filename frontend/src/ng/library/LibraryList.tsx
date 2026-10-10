import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ListFilter, Plus, Search } from "lucide-react";
import { useRoving } from "../graph/useRoving";
import { isTextField } from "../keys";
import { NodeIcon } from "../icons";
import { Menu } from "../ui/Menu";
import { Popover } from "../ui/Popover";
import { IdCard } from "../templates/menus/IdCard";
import { loadHidden, saveHidden } from "./kinds";
import { shownRows, type Row } from "./rows";
import { SECTIONS, SECTION_LABEL, type Section } from "./types";
import { tip } from "../ui/Tooltip";
import { PluginBadge } from "../templates/plugin";

/** What `+ New` can add: the section, and the node's or task's kind. */
const NEW: { label: string; section: Section; kind?: string }[] = [
  { label: "Exec node", section: "nodes", kind: "exec" },
  { label: "Gate", section: "nodes", kind: "gate" },
  { label: "Step", section: "steps" },
  { label: "Agent task", section: "tasks", kind: "agent" },
  { label: "Builtin task", section: "tasks", kind: "builtin" },
  { label: "Subprocess task", section: "tasks", kind: "subprocess" },
  { label: "Forge task", section: "tasks", kind: "forge" },
  { label: "Steering profile", section: "steering" },
];

function RowGlyph({ row }: { row: Row }) {
  if (row.glyph.gate) return <span className="lib-diamond" aria-hidden="true" />;
  return <NodeIcon name={row.glyph.icon} kind={row.glyph.taskKind} size={14} />;
}

const usedWord = (row: Row) => (row.used === "" ? "" : row.used);

/** The library's list (Decisions §10): grouped by kind, a kind filter and a
 *  search, `+ New` through an id step, a row per component with its used-by
 *  words, its change mark and a problem dot. */
export function LibraryList({ rows, selected, onSelect, onAdd }: {
  rows: Row[];
  selected: string | undefined;
  onSelect: (id: string) => void;
  /** Adds a component; resolves to the server's refusal, or null once it is in. */
  onAdd: (section: Section, name: string, kind?: string) => Promise<string | null>;
}) {
  const [q, setQ] = useState("");
  const [hidden, setHidden] = useState(loadHidden);
  const [filterOpen, setFilterOpen] = useState(false);
  const [adding, setAdding] = useState<(typeof NEW)[number] | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const search = useRef<HTMLInputElement>(null);
  const filterBtn = useRef<HTMLButtonElement>(null);
  const head = useRef<HTMLDivElement>(null);
  const shown = shownRows(rows, q, hidden);
  const roving = useRoving(selected, shown[0]?.id);

  // "/" jumps to the search, unless a field has the key.
  useEffect(() => {
    const on = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey || isTextField(e.target)) return;
      e.preventDefault();
      search.current?.focus();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  const toggle = (s: Section) => {
    const next = new Set(hidden);
    if (!next.delete(s)) next.add(s);
    setHidden(next);
    saveHidden(next);
  };

  const move = (e: KeyboardEvent, from: number) => {
    const to = e.key === "ArrowDown" ? from + 1 : e.key === "ArrowUp" ? from - 1 : e.key === "Home" ? 0 : e.key === "End" ? shown.length - 1 : null;
    if (to === null || !shown[to]) return;
    e.preventDefault();
    roving.go(shown[to].id);
  };

  const takenIn = (s: Section) => rows.filter((x) => x.section === s).map((x) => x.name);

  return (
    <aside className="lib-list" aria-label="Library">
      <div className="lib-head" ref={head}>
        <div className="lib-search">
          <Search size={12} aria-hidden />
          <input
            ref={search}
            className="lib-q"
            aria-label="Search the library"
            placeholder="Search the library"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setQ("");
              else if (e.key === "ArrowDown" && shown[0]) { e.preventDefault(); roving.go(shown[0].id); }
            }}
          />
        </div>
        <button ref={filterBtn} type="button" className="icon-btn" {...tip("Filter kinds")} aria-haspopup="dialog" aria-expanded={filterOpen} onClick={() => setFilterOpen((o) => !o)}>
          <ListFilter size={14} aria-hidden />
          {hidden.size > 0 && <span className="lib-filter-dot" aria-label={`${hidden.size} kind${hidden.size === 1 ? "" : "s"} hidden`} />}
        </button>
        <Menu
          label="New component"
          trigger={<Plus size={14} aria-hidden />}
          items={NEW.map((n) => ({ label: n.label, onSelect: () => { setRefused(null); setAdding(n); } }))}
        />
      </div>
      <Popover anchor={filterBtn} open={filterOpen} onClose={() => { setFilterOpen(false); filterBtn.current?.focus(); }} role="dialog" label="Show kinds">
        <div className="lib-filter">
          <p className="menu-title">Show kinds</p>
          {SECTIONS.map((s) => (
            <label key={s} className="lib-filter-row">
              <input type="checkbox" checked={!hidden.has(s)} onChange={() => toggle(s)} />
              <span>{SECTION_LABEL[s]}</span>
              <span className="lib-count">{rows.filter((x) => x.section === s).length}</span>
            </label>
          ))}
        </div>
      </Popover>
      {adding && (
        <IdCard
          anchor={head}
          title={`New ${adding.label.toLowerCase()}`}
          initial=""
          taken={takenIn(adding.section)}
          go="Add"
          refused={refused}
          onGo={async (name) => {
            const refusal = await onAdd(adding.section, name, adding.kind);
            if (refusal) setRefused(refusal);
            else setAdding(null);
          }}
          onClose={() => setAdding(null)}
        />
      )}
      <div className="lib-rows" role="listbox" aria-label="Library components">
        {!rows.length && <p className="lib-empty">The library is empty. + New to add the first component.</p>}
        {!!rows.length && !shown.length && <p className="lib-empty">No component matches.</p>}
        {SECTIONS.map((s) => {
          const inGroup = shown.filter((x) => x.section === s);
          if (!inGroup.length) return null;
          return (
            <div key={s} role="group" aria-label={SECTION_LABEL[s]}>
              <h2 className="lib-group">{SECTION_LABEL[s]} <span className="lib-count">{inGroup.length}</span></h2>
              {inGroup.map((row) => (
                <button
                  key={row.id}
                  ref={roving.ref(row.id)}
                  type="button"
                  role="option"
                  aria-selected={row.id === selected}
                  tabIndex={roving.tabIndex(row.id)}
                  className={`lib-row${row.id === selected ? " is-sel" : ""}`}
                  onClick={() => onSelect(row.id)}
                  onKeyDown={(e) => move(e, shown.indexOf(row))}
                >
                  <RowGlyph row={row} />
                  <span className="lib-name" data-allow-ellipsis title={row.name}>{row.name}</span>
                  {row.plugin && <PluginBadge plugin={row.plugin} />}
                  {row.problem && <span className="lib-prob" role="img" aria-label="has a problem" />}
                  {row.mark && <span className={`lib-mark is-${row.mark}`} role="img" aria-label={row.mark === "add" ? "added in the draft" : "changed in the draft"} />}
                  <span className="lib-used">{usedWord(row)}</span>
                </button>
              ))}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
