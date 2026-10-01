import { useEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { Box } from "lucide-react";
import { Popover } from "../../ui/Popover";
import { IdRow } from "./IdRow";

/** The two-step menu behind a seam on the chain canvas (Decisions §9 Adding a
 *  node): □ Exec node / ◇ Gate, then one row with the id and Create & open →. */
export function SeamMenu({ anchor, taken, refused, onCreate, onClose }: {
  anchor: RefObject<HTMLElement | null>;
  taken: string[];
  refused?: string | null;
  onCreate: (kind: "exec" | "gate", id: string) => void;
  onClose: () => void;
}) {
  const [kind, setKind] = useState<"exec" | "gate" | null>(null);
  const items = useRef<(HTMLButtonElement | null)[]>([]);
  useEffect(() => { if (!kind) items.current[0]?.focus(); }, [kind]);
  const onKey = (e: KeyboardEvent) => {
    const at = items.current.findIndex((b) => b === document.activeElement);
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    items.current[(at + 1) % 2]?.focus(); // two items: either arrow moves to the other
  };
  return (
    <Popover anchor={anchor} open onClose={onClose} role={kind ? "dialog" : "menu"} label={kind ? `New ${kind === "gate" ? "gate" : "exec node"}` : "Add here"}>
      {!kind ? (
        <div className="menu seam-menu" onKeyDown={onKey}>
          <button ref={(el) => void (items.current[0] = el)} type="button" role="menuitem" className="menu-item seam-item" onClick={() => setKind("exec")}>
            <Box size={14} aria-hidden /><span><span className="seam-item-name">Exec node</span><span className="seam-item-sub">runs steps of tasks</span></span>
          </button>
          <button ref={(el) => void (items.current[1] = el)} type="button" role="menuitem" className="menu-item seam-item" onClick={() => setKind("gate")}>
            <span className="seam-diamond" aria-hidden /><span><span className="seam-item-name">Gate</span><span className="seam-item-sub">waits for a person</span></span>
          </button>
        </div>
      ) : (
        <div className="seam-id">
          <IdRow label={kind === "gate" ? "Gate id" : "Node id"} taken={taken} placeholder={kind === "gate" ? "e.g. security_approval" : "e.g. security_scan"} go="Create & open →" refused={refused} onGo={(id) => onCreate(kind, id)} />
        </div>
      )}
    </Popover>
  );
}
