import { useEffect, useRef, useState, type RefObject } from "react";
import { KIND_ICON, type TaskKind } from "../../icons";
import { Popover } from "../../ui/Popover";
import { useLibrary } from "../useLibrary";
import { PickList } from "./PickList";

export type TaskChoice = { kind: TaskKind } | { extends: string };
const KINDS: TaskKind[] = ["agent", "builtin", "subprocess", "forge"];

/** "From the library…" or a blank task (Decisions §9 First step). `agentOnly`
 *  for the judge, escalation and reviewer slots. */
export function TaskMenu({ anchor, title, agentOnly, onPick, onClose }: { anchor: RefObject<HTMLElement | null>; title: string; agentOnly?: boolean; onPick: (c: TaskChoice) => void; onClose: () => void }) {
  const [lib, setLib] = useState(false);
  const library = useLibrary();
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (!lib) first.current?.focus(); }, [lib]);
  const tasks = typeof library === "string" ? [] : library.filter((c) => c.kind === "tasks" && (!agentOnly || c.definition.kind === "agent"));
  const kinds = agentOnly ? (["agent"] as TaskKind[]) : KINDS;
  return (
    <Popover anchor={anchor} open onClose={onClose} role="dialog" label={title}>
      <div className="task-menu">
        <p className="menu-title">{title}</p>
        {lib ? (
          library === "failed" ? <p className="picklist-empty">Couldn't load the library.</p> : (
            <PickList
              placeholder="Search the library"
              empty={library === "loading" ? "Loading…" : "No library task matches."}
              items={tasks.map((c) => {
                const k = String(c.definition.kind ?? "agent") as TaskKind;
                const Icon = KIND_ICON[k] ?? KIND_ICON.agent;
                return { key: c.name, label: c.name, sub: String(c.definition.prompt ?? c.definition.target ?? c.definition.command ?? k).split("\n")[0].slice(0, 80), icon: <Icon size={14} aria-hidden /> };
              })}
              onPick={(name) => onPick({ extends: name })}
            />
          )
        ) : (
          <div className="menu" role="menu" onKeyDown={(e) => {
            if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
            e.preventDefault();
            const items = [...e.currentTarget.querySelectorAll<HTMLButtonElement>("button")];
            const at = items.indexOf(document.activeElement as HTMLButtonElement);
            items[(at + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
          }}>
            <button ref={first} type="button" role="menuitem" className="menu-item" onClick={() => setLib(true)}>From the library…</button>
            {kinds.map((k) => {
              const Icon = KIND_ICON[k];
              return (
                <button key={k} type="button" role="menuitem" className="menu-item seam-item" onClick={() => onPick({ kind: k })}>
                  <Icon size={14} aria-hidden /><span className="seam-item-name">Blank {k} task</span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </Popover>
  );
}
