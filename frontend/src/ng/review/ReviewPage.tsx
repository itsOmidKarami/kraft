import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Placeholder } from "../shell/Placeholder";
import { usePageItem } from "../shell/pageItem";
import { ItemHeader } from "../item/header/ItemHeader";
import { placeUrl } from "../item/url";
import { useItem, type ItemDetail } from "../item/useItem";
// The item header's styles live with it; this page can be the first one loaded.
import "../item/item.css";
import "./review.css";

/** `/ng/work-items/:id/review`: the changes of one item, its threads, and the
 *  review that sends them (W8, spec §6.4). */
export function ReviewPage() {
  const { id = "" } = useParams();
  const loaded = useItem(id);
  const setPageItem = usePageItem((s) => s.set);
  const item = loaded.state === "ready" ? loaded.item : null;
  useEffect(() => {
    setPageItem(item);
    return () => setPageItem(null);
  }, [item, setPageItem]);
  if (loaded.state === "loading") return <div className="review-page" aria-busy="true" />;
  if (loaded.state === "missing")
    return <Placeholder label="Work item not found" note={`There is no work item ${id}. It may have been removed.`} />;
  return <Review item={loaded.item} reload={loaded.reload} />;
}

function Review({ item, reload }: { item: ItemDetail; reload: () => void }) {
  const navigate = useNavigate();
  const toItem = (p: Parameters<typeof placeUrl>[1]) => navigate(placeUrl(item.id, p));
  return (
    <div className="review-page">
      <h1 className="review-visually-hidden">Review changes: {item.title}</h1>
      <ItemHeader item={item} reload={reload} onSettings={() => toItem({ sel: { kind: "chain" }, tab: "config" })} onRunLog={() => toItem({ sel: { kind: "chain" } })} />
    </div>
  );
}
