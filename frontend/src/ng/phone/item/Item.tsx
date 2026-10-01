import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useEvents } from "../../item/useEvents";
import { useItem } from "../../item/useItem";
import { ScreenHeader } from "../nav/ScreenHeader";
import { Composer, isComposeKind } from "./Composer";
import { ItemScreen } from "./ItemScreen";

/** `/work-items/:id`: the item screen, or a composer over it (`?compose=`). */
export function Item() {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const loaded = useItem(id);
  const version = loaded.state === "ready" ? loaded.item.updated_at : "";
  const events = useEvents(id, version);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);
  if (loaded.state === "loading")
    return (
      <>
        <ScreenHeader />
        <div className="ph-content ph-skeleton" aria-busy="true"><span /><span /><span /></div>
      </>
    );
  if (loaded.state === "missing")
    return (
      <>
        <ScreenHeader />
        <div className="ph-content"><h1 className="ph-title">Not found</h1><p className="ph-empty">This work item does not exist.</p></div>
      </>
    );
  const compose = params.get("compose");
  if (isComposeKind(compose)) return <Composer item={loaded.item} kind={compose} reload={loaded.reload} />;
  return <ItemScreen item={loaded.item} events={events} reload={loaded.reload} now={now} />;
}
