import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Placeholder } from "../shell/Placeholder";
import { usePageItem } from "../shell/pageItem";
import { Banner, QuestionCard } from "./Banner";
import { ItemDraftProvider } from "./draft/context";
import { useSelect } from "./draft/select";
import { LeaveGuard } from "./draft/LeaveDialog";
import { ReviewDialog } from "./draft/ReviewDialog";
import { ItemHeader, useDuplicate } from "./header/ItemHeader";
import { PausedCard, StateCard } from "./StateCard";
import { Brief, DiffLine, Title } from "./Top";
import { ESCALATION } from "./nodeGraph";
import { placeUrl } from "./url";
import { openBudgetEditor, Workspace } from "./Workspace";
import { openLimitEditor } from "./RaiseLimit";
import { runVersion, useItem, type ItemDetail } from "./useItem";
import "./item.css";

/** `/work-items/:id[/nodes/:node]`: one item, its chain and the pane (W5). */
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
  return (
    <ItemDraftProvider item={loaded.item} reload={loaded.reload}>
      <Item item={loaded.item} version={loaded.version} reload={loaded.reload} />
      <ReviewDialog />
      <LeaveGuard />
    </ItemDraftProvider>
  );
}

function Item({ item, version, reload }: { item: ItemDetail; version: string; reload: () => void }) {
  const navigate = useNavigate();
  const { node: nodeView } = useParams();
  const [cancelling, setCancelling] = useState(false);
  const [escalating, setEscalating] = useState(false);
  const [cardError, setCardError] = useState<string | null>(null);
  const duplicate = useDuplicate(item.id, setCardError);
  const openGate = useSelect(item.id);
  const openNode = (node: string) => navigate(placeUrl(item.id, { sel: { kind: "node", node } }));
  const settings = () => navigate(placeUrl(item.id, { sel: { kind: "chain" }, tab: "config" }));
  // Raise cap (the banner's and the header's) opens the banner's editor on a stop that names its limit (R12b-06),
  // and the budget editor on a budget stop, as the peek's does (R11a-05).
  const raise = () => {
    if (item.stop?.limit) return openLimitEditor(item.id);
    if (item.stop?.kind === "budget") openBudgetEditor(item.id);
    settings();
  };
  const threadOf = () => item.stop?.node && navigate(placeUrl(item.id, { node: item.stop.node, sel: { kind: "task", node: item.stop.node, step: ESCALATION, task: ESCALATION } }));
  // The header's Answer: the question card's box when it is on the page, else the thread the question is in.
  const answer = () => {
    const box = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Your answer"]');
    if (box) return box.focus();
    threadOf();
  };
  const runLog = () => {
    const node = item.chain_definition.nodes.find((n) => n.id === item.current_node_id);
    const last = [...item.worker_sessions].reverse().find((s) => s.node_id === node?.id && s.hook_point.split(".").length === 3);
    if (!node || !last) return settings();
    const [, step, task] = last.hook_point.split(".");
    navigate(placeUrl(item.id, { node: node.id, sel: { kind: "task", node: node.id, step, task }, tab: "log" }));
  };
  return (
    <div className="item-page">
      <ItemHeader item={item} reload={reload} onSettings={settings} onRaise={raise} onGate={openGate} onAnswer={answer} onRunLog={runLog} cancelOpen={cancelling} onCancelOpen={setCancelling} escalateOpen={escalating} onEscalateOpen={setEscalating} />
      <div className="item-top">
        <Title id={item.id} title={item.title} onSaved={reload} />
        {!nodeView && <Brief id={item.id} brief={item.description ?? ""} onSaved={reload} />}
        <DiffLine id={item.id} version={runVersion(item)} />
      </div>
      <Banner item={item} onOpenGate={openGate} onRaise={raise} reload={reload} />
      <StateCard item={item} reload={reload} onCancel={() => setCancelling(true)} onEscalate={() => setEscalating(true)} onDuplicate={duplicate} onOpenNode={openNode} />
      {cardError && <p className="item-error" role="alert">{cardError}</p>}
      <PausedCard item={item} reload={reload} />
      <QuestionCard item={item} compact={!!nodeView} reload={reload} onOpenThread={threadOf} />
      <Workspace item={item} version={version} reload={reload} />
    </div>
  );
}
