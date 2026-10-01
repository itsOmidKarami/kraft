import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Placeholder } from "../shell/Placeholder";
import { usePageItem } from "../shell/pageItem";
import { Banner, QuestionCard } from "./Banner";
import { ItemHeader, useDuplicate } from "./header/ItemHeader";
import { PausedCard, StateCard } from "./StateCard";
import { Brief, DiffLine, Title } from "./Top";
import { placeUrl } from "./url";
import { Workspace } from "./Workspace";
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
  const { node: nodeView } = useParams();
  const [cancelling, setCancelling] = useState(false);
  const [escalating, setEscalating] = useState(false);
  const [cardError, setCardError] = useState<string | null>(null);
  const duplicate = useDuplicate(item.id, setCardError);
  const openNode = (node: string) => navigate(placeUrl(item.id, { sel: { kind: "node", node } }));
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
      <ItemHeader item={item} reload={reload} onSettings={settings} onRunLog={runLog} cancelOpen={cancelling} onCancelOpen={setCancelling} escalateOpen={escalating} onEscalateOpen={setEscalating} />
      <div className="item-top">
        <Title id={item.id} title={item.title} onSaved={reload} />
        {!nodeView && <Brief id={item.id} brief={item.description ?? ""} onSaved={reload} />}
        <DiffLine id={item.id} version={item.updated_at} />
      </div>
      <Banner item={item} onOpenGate={(gate) => navigate(placeUrl(item.id, { sel: { kind: "node", node: gate } }))} onRaise={settings} />
      <StateCard item={item} reload={reload} onCancel={() => setCancelling(true)} onEscalate={() => setEscalating(true)} onDuplicate={duplicate} onOpenNode={openNode} />
      {cardError && <p className="item-error" role="alert">{cardError}</p>}
      <PausedCard item={item} reload={reload} />
      <QuestionCard item={item} compact={!!nodeView} reload={reload} onOpenThread={() => item.stop?.node && navigate(placeUrl(item.id, { node: item.stop.node, sel: { kind: "node", node: item.stop.node }, tab: "thread" }))} />
      <Workspace item={item} reload={reload} />
    </div>
  );
}
