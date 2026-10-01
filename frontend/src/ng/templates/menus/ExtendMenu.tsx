import type { RefObject } from "react";
import { Layers } from "lucide-react";
import { Popover } from "../../ui/Popover";
import { useLibrary } from "../useLibrary";
import { PickList } from "./PickList";

/** "extend a node from the library" (Decisions §9 Extend from library): a
 *  searchable menu of library nodes. Picking one fills the canvas with its steps. */
export function ExtendMenu({ anchor, title = "Extend a library node", note = "Edits you make afterwards override it for this chain.", onPick, onClose }: { anchor: RefObject<HTMLElement | null>; title?: string; note?: string; onPick: (base: string) => void; onClose: () => void }) {
  const library = useLibrary();
  const nodes = typeof library === "string" ? [] : library.filter((c) => c.kind === "nodes");
  const summary = (d: Record<string, unknown>) => {
    const steps = (d.steps as { id: string; tasks?: { id: string }[] }[] | undefined) ?? (d.tasks ? [{ id: "main", tasks: d.tasks as { id: string }[] }] : []);
    return steps.map((s) => (s.id === "main" ? (s.tasks ?? []).map((t) => t.id).join(", ") : s.id)).join(" → ") + (d.fix_loop ? " · fix loop" : "");
  };
  return (
    <Popover anchor={anchor} open onClose={onClose} role="dialog" label={title}>
      <div className="task-menu">
        <p className="menu-title">{title}</p>
        <p className="tpl-menu-note">{note}</p>
        {library === "failed" ? <p className="picklist-empty">Couldn't load the library.</p> : (
          <PickList
            placeholder="Search library nodes"
            empty={library === "loading" ? "Loading…" : "No library node matches."}
            items={nodes.map((c) => ({ key: c.name, label: c.name, sub: summary(c.definition), icon: <Layers size={14} aria-hidden /> }))}
            onPick={onPick}
          />
        )}
      </div>
    </Popover>
  );
}
