import { useRef, useState } from "react";
import { ExtendMenu } from "../../templates/menus/ExtendMenu";
import { IdRow } from "../../templates/menus/IdRow";
import { Popover } from "../../ui/Popover";
import { useDraft } from "./context";
import { useSelect } from "./select";
import { addNode } from "./view";

/** The menu behind a `+` seam on the item's chain (Decisions §5 Adding a node):
 *  pick a library node, give it an id, **Create & open →**. The API's `add_node`
 *  takes library components only, so there is no blank node or gate row (Decided 6). */
export function AddNodeMenu({ at, seam, onClose }: { at: number; seam: HTMLElement; onClose: () => void }) {
  const d = useDraft()!;
  const select = useSelect(d.raw.id);
  const anchor = useRef<HTMLElement | null>(seam);
  const [base, setBase] = useState<string | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const nodes = d.shown.chain_definition.nodes;
  const after = nodes[at - 1]?.id;
  const close = () => {
    onClose();
    seam.focus();
  };
  const create = async (id: string) => {
    if (!base || !after) return;
    const a = await d.draft.edit((ops) => addNode(ops, after, id, base), id);
    if (!a || a.status !== 200) return setRefused(String((a?.body as { detail?: unknown } | undefined)?.detail ?? "The draft could not be saved."));
    onClose();
    // An op the server could not apply is not on the canvas; its problem is in the chain pane.
    const placed = (a.body as { nodes?: { id: string }[] }).nodes?.some((n) => n.id === id);
    select(placed ? id : null);
  };
  if (!base) return <ExtendMenu anchor={anchor} title="Add a library node" note={`Runs after ${after}. The new node lands in this item's draft.`} onPick={setBase} onClose={close} />;
  return (
    <Popover anchor={anchor} open onClose={close} role="dialog" label="New node">
      <div className="idr-add-id">
        <IdRow label="Node id" initial={base} taken={nodes.map((n) => n.id)} placeholder="e.g. security_scan" go="Create & open →" refused={refused} onGo={create} />
      </div>
    </Popover>
  );
}
