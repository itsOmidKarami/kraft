import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Placeholder } from "../shell/Placeholder";
import { usePageItem } from "../shell/pageItem";
import { ItemHeader } from "./header/ItemHeader";
import { placeUrl } from "./url";
import { useItem, type ItemDetail } from "./useItem";
import "./item.css";

/** `/ng/work-items/:id[/nodes/:node]`: one item, its chain and the pane (W5). */
export function ItemPage() {
  const { id = "" } = useParams();
  const loaded = useItem(id);
  const setPageItem = usePageItem((s) => s.set);
  const item = loaded.state === "ready" ? loaded.item : null;
  useEffect(() => {
    setPageItem(item);
    return () => setPageItem(null);
  }, [item, setPageItem]);
  if (loaded.state === "loading") return <div className="item-page" aria-busy="true" />;
  if (loaded.state === "missing")
    return <Placeholder label="Work item not found" note={`There is no work item ${id}. It may have been removed.`} />;
  return <Item item={loaded.item} reload={loaded.reload} />;
}

function Item({ item, reload }: { item: ItemDetail; reload: () => void }) {
  const navigate = useNavigate();
  const settings = () => navigate(placeUrl(item.id, { sel: { kind: "chain" }, tab: "config" }));
  const runLog = () => {
    const node = item.chain_definition.nodes.find((n) => n.id === item.current_node_id);
    const last = [...item.worker_sessions].reverse().find((s) => s.node_id === node?.id && s.hook_point.split(".").length === 3);
    if (!node || !last) return settings();
    const [, step, task] = last.hook_point.split(".");
    navigate(placeUrl(item.id, { node: node.id, sel: { kind: "task", node: node.id, step, task }, tab: "log" }));
  };
  return (
    <div className="item-page">
      <ItemHeader item={item} reload={reload} onSettings={settings} onRunLog={runLog} />
      <div className="item-top">
        <h1 className="item-title">{item.title}</h1>
      </div>
      <div className="item-canvas" />
    </div>
  );
}
