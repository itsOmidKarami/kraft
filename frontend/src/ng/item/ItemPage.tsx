import { useParams } from "react-router-dom";
import { Placeholder } from "../shell/Placeholder";
import { useItem, type ItemDetail } from "./useItem";
import "./item.css";

/** `/ng/work-items/:id[/nodes/:node]`: one item, its chain and the pane (W5). */
export function ItemPage() {
  const { id = "" } = useParams();
  const loaded = useItem(id);
  if (loaded.state === "loading") return <div className="item-page" aria-busy="true" />;
  if (loaded.state === "missing")
    return <Placeholder label="Work item not found" note={`There is no work item ${id}. It may have been removed.`} />;
  return <Item item={loaded.item} reload={loaded.reload} />;
}

function Item({ item }: { item: ItemDetail; reload: () => void }) {
  return (
    <div className="item-page">
      <div className="item-top">
        <h1 className="item-title">{item.title}</h1>
      </div>
      <div className="item-canvas" />
    </div>
  );
}
