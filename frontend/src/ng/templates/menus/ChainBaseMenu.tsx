import { useEffect, useState, type RefObject } from "react";
import { Workflow } from "lucide-react";
import * as api from "../../../api";
import type { TemplateSummary } from "../../../types";
import { pickerMark } from "../../library/types";
import { Popover } from "../../ui/Popover";
import { Empty } from "../../ui/Empty";
import { PickList } from "./PickList";

/** Change base of a chain that extends another: a searchable menu of the chains it
 *  may extend instead. Not itself, its parent, a chain that extends it (a cycle), a
 *  repo library's or one that doesn't resolve; the server refuses those too. */
export function ChainBaseMenu({ anchor, chain, parent, onPick, onClose }: { anchor: RefObject<HTMLElement | null>; chain: string; parent: string; onPick: (base: string) => void; onClose: () => void }) {
  const [chains, setChains] = useState<TemplateSummary[] | "loading" | "failed">("loading");
  useEffect(() => {
    api.getTemplates().then(setChains).catch(() => setChains("failed"));
  }, []);
  const list = typeof chains === "string" ? [] : chains;
  const below = new Set(list.find((c) => c.id === chain)?.extended_by ?? []);
  const items = list
    .filter((c) => c.id !== chain && c.id !== parent && !below.has(c.id) && !c.addon?.repo && !c.error)
    .map((c) => ({ key: c.id, label: c.id, sub: `${c.nodes.length} nodes${c.extends ? ` · extends ${c.extends}` : ""}${pickerMark(c.addon)}`, icon: <Workflow size={14} aria-hidden /> }));
  return (
    <Popover anchor={anchor} open onClose={onClose} role="dialog" label="Change base">
      <div className="task-menu">
        <p className="menu-title">Change base</p>
        <p className="tpl-menu-note">Next, you'll see which of this chain's edits fit the new base.</p>
        {chains === "failed" ? <Empty className="picklist-empty">Couldn't load the chains.</Empty> : (
          <PickList placeholder="Search chains" empty={chains === "loading" ? "Loading…" : "No other chain to extend."} items={items} onPick={onPick} />
        )}
      </div>
    </Popover>
  );
}
