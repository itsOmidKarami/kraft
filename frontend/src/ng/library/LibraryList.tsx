import { useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { ChevronDown, ChevronRight, ListFilter, Plus, Search } from "lucide-react";
import { useRoving } from "../graph/useRoving";
import { isTextField } from "../keys";
import { NodeIcon } from "../icons";
import { Menu } from "../ui/Menu";
import { Popover } from "../ui/Popover";
import { IdCard } from "../templates/menus/IdCard";
import { loadHidden, saveHidden } from "./kinds";
import { LOCAL, shownRows, sourceLabel, sources, type Row } from "./rows";
import type { ListSize } from "./listWidth";
import { SECTIONS, SECTION_LABEL, type Section } from "./types";
import { tip } from "../ui/Tooltip";
import { NoMatch } from "../ui/NoMatch";
import { AddonBadge } from "../templates/addon";
import { SEARCH_LIBRARY } from "./sharedWords";
import { Empty } from "../ui/Empty";
import { LinkButton } from "../ui/LinkButton";

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

/** The heading each `+ New` item sits under. */
const NEW_GROUP: Record<Section, string> = { nodes: "Node", steps: "Step", tasks: "Task", steering: "Steering" };

function RowGlyph({ row }: { row: Row }) {
  if (row.glyph.gate) return <span className="lib-diamond" aria-hidden="true" />;
  return <NodeIcon name={row.glyph.icon} kind={row.glyph.taskKind} size={14} />;
}

/** The library's list (Decisions §10): grouped by kind, a filter by kind and by source (local or an add-on) and a
 *  search, `+ New` through an id step, a row per component with its used-by
 *  words, its change mark and a problem dot. Its right edge resizes it. */
export function LibraryList({ rows, selected, only, size, onShowAll, onSelect, onAdd }: {
  rows: Row[];
  /** From useListWidth: the list's width and its handle's props. */
  size: ListSize;
  selected: string | undefined;
  /** A section a landing tile asked for: the list shows only it, until Show all. */
  only?: Section;
  onShowAll: () => void;
  onSelect: (id: string) => void;
  /** Adds a component; resolves to the server's refusal, or null once it is in. */
  onAdd: (section: Section, name: string, kind?: string) => Promise<string | null>;
}) {
  const [q, setQ] = useState("");
  const [hidden, setHidden] = useState(loadHidden);
  // Sources the person turned off, for this visit: until the published list names an add-on its source is the bare
  // namespace, so none is remembered, and one that no longer names a source is dropped (`hiddenSources` below).
  const [turnedOff, setHiddenSources] = useState<ReadonlySet<string>>(new Set());
  const [filterOpen, setFilterOpen] = useState(false);
  const [adding, setAdding] = useState<(typeof NEW)[number] | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const search = useRef<HTMLInputElement>(null);
  const filterBtn = useRef<HTMLButtonElement>(null);
  const head = useRef<HTMLDivElement>(null);
  // Groups the person folded; a search or the selection's own group opens one again.
  const [folded, setFolded] = useState<ReadonlySet<Section>>(new Set());
  // A tile asked for its section, so the kind filter does not empty it.
  const origins = useMemo(() => sources(rows), [rows]);
  const hiddenSources = useMemo(() => new Set([...turnedOff].filter((s) => origins.some((o) => o.source === s))), [turnedOff, origins]);
  const kindsOff = only ? new Set<Section>() : hidden;
  // What a section tile narrowed to is the base the filters and the search work on.
  const base = only ? rows.filter((x) => x.section === only) : rows;
  const shown = shownRows(base, q, kindsOff, hiddenSources);
  // What the filters alone hide, apart from the search.
  const filtered = base.length - shownRows(base, "", kindsOff, hiddenSources).length;
  const selectedSection = rows.find((x) => x.id === selected)?.section;
  const isFolded = (s: Section) => folded.has(s) && !q.trim();
  const reachable = shown.filter((x) => !isFolded(x.section));
  const roving = useRoving(selected, reachable[0]?.id);
  useEffect(() => {
    if (selectedSection) setFolded((f) => (f.has(selectedSection) ? new Set([...f].filter((x) => x !== selectedSection)) : f));
  }, [selectedSection, selected]);

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

  const toggleSource = (s: string) => setHiddenSources((h) => new Set(h.has(s) ? [...h].filter((x) => x !== s) : [...h, s]));
  /** Turns the filters off: the kinds too, unless a section tile has the list ignoring them. */
  const reset = (kinds = true) => {
    if (kinds) {
      setHidden(new Set());
      saveHidden(new Set());
    }
    setHiddenSources(new Set());
  };
  // The filters in force: a kind is not one while a tile's section shows whatever it says.
  const active = kindsOff.size + hiddenSources.size;
  const kindsBox = useRef<HTMLDivElement>(null);

  const move = (e: KeyboardEvent, from: number) => {
    const to = e.key === "ArrowDown" ? from + 1 : e.key === "ArrowUp" ? from - 1 : e.key === "Home" ? 0 : e.key === "End" ? reachable.length - 1 : null;
    if (to === null || !reachable[to]) return;
    e.preventDefault();
    roving.go(reachable[to].id);
  };

  const takenIn = (s: Section) => rows.filter((x) => x.section === s).map((x) => x.name);

  return (
    <aside className="lib-list" aria-label="Library" style={{ "--lib-w": `${size.width}px` } as CSSProperties}>
      <div className="lib-head" ref={head}>
        <div className="lib-search">
          <Search size={12} aria-hidden />
          <input
            ref={search}
            className="lib-q"
            aria-label={SEARCH_LIBRARY}
            placeholder={SEARCH_LIBRARY}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setQ("");
              else if (e.key === "ArrowDown" && reachable[0]) { e.preventDefault(); roving.go(reachable[0].id); }
            }}
          />
        </div>
        <button ref={filterBtn} type="button" className="icon-btn" {...tip("Filter")} aria-haspopup="dialog" aria-expanded={filterOpen} onClick={() => setFilterOpen((o) => !o)}>
          <ListFilter size={14} aria-hidden />
          {active > 0 && <span className="lib-filter-dot" role="img" aria-label={`${active} filter${active === 1 ? "" : "s"} on`} />}
        </button>
        <Menu
          label="New component"
          trigger={<Plus size={14} aria-hidden />}
          items={NEW.map((n) => ({ label: n.label, onSelect: () => { setRefused(null); setAdding(n); }, group: NEW_GROUP[n.section] }))}
        />
      </div>
      <Popover anchor={filterBtn} open={filterOpen} onClose={() => { setFilterOpen(false); filterBtn.current?.focus(); }} role="dialog" label="Filter the library">
        <div className="lib-filter" ref={kindsBox}>
          <div className="lib-filter-head">
            <p className="menu-title">Filter</p>
            {hidden.size + hiddenSources.size > 0 && <LinkButton onClick={() => { reset(); kindsBox.current?.querySelector("input")?.focus(); }}>Reset</LinkButton>}
          </div>
          <fieldset className="lib-filter-set">
            <legend className="lib-filter-legend">Kind</legend>
            {SECTIONS.filter((s) => rows.some((x) => x.section === s)).map((s) => (
              <label key={s} className="lib-filter-row">
                <input type="checkbox" checked={!hidden.has(s)} onChange={() => toggle(s)} />
                <span>{SECTION_LABEL[s]}</span>
                <span className="lib-count">{rows.filter((x) => x.section === s).length}</span>
              </label>
            ))}
          </fieldset>
          {/* With nothing from an add-on there is no source to choose between. */}
          {origins.some((o) => o.source !== LOCAL) && (
            <fieldset className="lib-filter-set">
              <legend className="lib-filter-legend">Source</legend>
              {origins.map((o) => (
                <label key={o.source} className="lib-filter-row">
                  <input type="checkbox" checked={!hiddenSources.has(o.source)} onChange={() => toggleSource(o.source)} />
                  <span className={`lib-filter-name${o.source === LOCAL ? "" : " is-id"}`} data-allow-ellipsis title={sourceLabel(o.source)}>{sourceLabel(o.source)}</span>
                  <span className="lib-count">{o.count}</span>
                </label>
              ))}
            </fieldset>
          )}
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
      {only && (
        <p className="lib-only">
          <span>Showing {SECTION_LABEL[only].toLowerCase()} only</span>
          <LinkButton onClick={onShowAll}>Show all</LinkButton>
        </p>
      )}
      {filtered > 0 && (
        <p className="lib-only">
          <span>Hiding {filtered} of {base.length}</span>
          <LinkButton onClick={() => { reset(!only); filterBtn.current?.focus(); }}>Reset filters</LinkButton>
        </p>
      )}
      <div className="lib-rows" role="group" aria-label="Library components">
        {!rows.length && <Empty className="lib-empty">The library is empty. + New to add the first component.</Empty>}
        {!!rows.length && !shown.length && <NoMatch className="lib-empty" query={q} onClear={() => { setQ(""); if (!q.trim()) reset(); }} />}
        {SECTIONS.map((s) => {
          const inGroup = shown.filter((x) => x.section === s);
          if (!inGroup.length) return null;
          return (
            <div key={s}>
              <h2 className="lib-group">
                <button type="button" className="lib-group-btn" aria-expanded={!isFolded(s)} onClick={() => setFolded((f) => new Set(f.has(s) ? [...f].filter((x) => x !== s) : [...f, s]))}>
                  {isFolded(s) ? <ChevronRight size={12} aria-hidden /> : <ChevronDown size={12} aria-hidden />}
                  {SECTION_LABEL[s]} <span className="lib-count">{inGroup.length}</span>
                </button>
              </h2>
              {!isFolded(s) && <div role="listbox" aria-label={SECTION_LABEL[s]}>{inGroup.map((row) => (
                <button
                  key={row.id}
                  ref={roving.ref(row.id)}
                  type="button"
                  role="option"
                  aria-selected={row.id === selected}
                  tabIndex={roving.tabIndex(row.id)}
                  className={`lib-row${row.id === selected ? " is-sel" : ""}`}
                  onClick={() => onSelect(row.id)}
                  onKeyDown={(e) => move(e, reachable.indexOf(row))}
                >
                  <RowGlyph row={row} />
                  <span className="lib-name" data-allow-ellipsis title={row.name}>{row.name}</span>
                  {row.addon && <AddonBadge addon={row.addon} />}
                  {row.problem && <span className="lib-prob" role="img" aria-label="has a problem" />}
                  {row.mark && <span className={`lib-mark is-${row.mark}`} role="img" aria-label={row.mark === "add" ? "added in the draft" : "changed in the draft"} />}
                  {row.used && <span className={`lib-used${row.used === "unused" ? " is-unused" : ""}`}>{row.used}</span>}
                </button>
              ))}</div>}
            </div>
          );
        })}
      </div>
      {size.handle && <div className="lib-handle" {...size.handle} />}
    </aside>
  );
}
