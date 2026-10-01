import { useRef, useState, type KeyboardEvent } from "react";
import { NodeIcon } from "../icons";
import { ACCESS_WORD, type HProblem, type Resolved, problemsOfHarness, problemsOfProfile } from "./model";

export type Pick = { kind: "harness" | "profile"; id: string };

/** The list beside the canvas (Decisions §11): Harnesses first, then Profiles.
 *  One roving tab stop; ↑/↓ move, Enter selects. */
export function List({ r, problems, sel, onPick, onAdd }: {
  r: Resolved;
  problems: HProblem[];
  sel: Pick | null;
  onPick: (p: Pick) => void;
  onAdd: () => void;
}) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const match = (id: string) => id.toLowerCase().includes(q.trim().toLowerCase());
  const harnesses = r.harnesses.filter((h) => match(h.id));
  const profiles = Object.keys(r.profiles).filter(match);
  const keyOf = (p: Pick) => `${p.kind}:${p.id}`;
  const rows: Pick[] = [...harnesses.map((h) => ({ kind: "harness" as const, id: h.id })), ...profiles.map((id) => ({ kind: "profile" as const, id }))];
  const tab = (p: Pick) => (keyOf(p) === (active ?? (sel ? keyOf(sel) : rows[0] && keyOf(rows[0]))) ? 0 : -1);

  const move = (e: KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const els = [...(box.current?.querySelectorAll<HTMLElement>("[data-row]") ?? [])];
    const at = els.indexOf(document.activeElement as HTMLElement);
    if (at < 0) return;
    e.preventDefault();
    const next = els[(at + (e.key === "ArrowDown" ? 1 : -1) + els.length) % els.length];
    setActive(next.dataset.row ?? null);
    next.focus();
  };

  return (
    <nav className="hn-list" aria-label="Harnesses and profiles" ref={box} onKeyDown={move}>
      <input className="hn-search" type="search" aria-label="Search harnesses" placeholder="Search harnesses" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="hn-list-body">
        <h3 className="hn-head">Harnesses · {r.harnesses.length}</h3>
        {harnesses.map((h) => {
          const bad = problemsOfHarness(r, problems, h.id).length > 0;
          const p: Pick = { kind: "harness", id: h.id };
          return (
            <button key={h.id} type="button" aria-label={`${h.id}, ${ACCESS_WORD[h.state]}${bad ? ", has a problem" : ""}`} data-row={keyOf(p)} tabIndex={tab(p)} className={`hn-row${sel && keyOf(sel) === keyOf(p) ? " is-sel" : ""}`} aria-current={sel && keyOf(sel) === keyOf(p) ? "true" : undefined} onClick={() => onPick(p)}>
              <NodeIcon name="bot" size={14} />
              <span className="hn-row-name">{h.id}</span>
              {bad && <span className="hn-dot" aria-hidden />}
              <span className="hn-row-meta" data-access={h.state}>{ACCESS_WORD[h.state]}</span>
            </button>
          );
        })}
        <h3 className="hn-head">Profiles · {Object.keys(r.profiles).length}</h3>
        {profiles.map((id) => {
          const bad = problemsOfProfile(r, problems, id).length > 0;
          const n = r.profiles[id].tasks.length;
          const p: Pick = { kind: "profile", id };
          return (
            <button key={id} type="button" aria-label={`${id}, ${n} ${n === 1 ? "task" : "tasks"}${bad ? ", has a problem" : ""}`} data-row={keyOf(p)} tabIndex={tab(p)} className={`hn-row${sel && keyOf(sel) === keyOf(p) ? " is-sel" : ""}`} aria-current={sel && keyOf(sel) === keyOf(p) ? "true" : undefined} onClick={() => onPick(p)}>
              <NodeIcon name="layers" size={14} />
              <span className="hn-row-name">{id}</span>
              {bad && <span className="hn-dot" aria-hidden />}
              <span className="hn-row-meta">{n} {n === 1 ? "task" : "tasks"}</span>
            </button>
          );
        })}
        {rows.length === 0 && <p className="hn-empty">Nothing matches "{q}".</p>}
        <button type="button" className="hn-row hn-add" onClick={onAdd}>+ Profile</button>
      </div>
    </nav>
  );
}
