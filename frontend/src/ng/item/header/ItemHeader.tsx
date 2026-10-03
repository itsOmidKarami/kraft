import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { elapsedBetween } from "../../../format";
import { useGroupCount } from "../../board/counts";
import { Clock, EllipsisVertical } from "../../icons";
import { HeaderActions } from "../../shell/HeaderActions";
import { Menu, type MenuItem } from "../../ui/Menu";
import { Popover } from "../../ui/Popover";
import { showToast } from "../../ui/Toast";
import { act } from "../actions";
import { useDraft } from "../draft/context";
import { DraftState, ReviewButton } from "../draft/DraftBar";
import { actionPath } from "../paths";
import { archivable, headerState, type PanelItem } from "../status";
import type { ItemDetail } from "../useItem";
import { CancelCard } from "./CancelCard";
import { CompleteDialog, EscalateDialog, PauseConfirm } from "./Dialogs";
import { MainButton } from "./MainButton";

const ENDED = new Set(["done", "cancelled", "archived"]);

/** Duplicate (B3): file the copy, open it, and say if it looks like a duplicate of another open item. */
export function useDuplicate(id: string, onError: (e: string) => void) {
  const navigate = useNavigate();
  return async () => {
    const r = await act.duplicate(id);
    if (!r.ok) return onError(r.error);
    if (r.body.duplicate_warning) showToast(r.body.duplicate_warning, 6000);
    navigate(`/work-items/${encodeURIComponent(r.body.id)}`);
  };
}

type Props = {
  item: ItemDetail;
  reload: () => void;
  /** Select the chain and open its Config tab (Item settings, the capped Resume). */
  onSettings: () => void;
  /** Select the current node's latest task, Log tab. */
  onRunLog: () => void;
  /** Outside triggers for the cancel card (the MR-closed state card's Cancel item…). */
  cancelOpen?: boolean;
  onCancelOpen?: (open: boolean) => void;
  escalateOpen?: boolean;
  onEscalateOpen?: (open: boolean) => void;
};

/** The right side of the item page's header row (Decisions §1, §14): others
 *  need you, elapsed, the badge, the main button with its panel, and ⋮. */
export function ItemHeader({ item, reload, onSettings, onRunLog, cancelOpen, onCancelOpen, escalateOpen, onEscalateOpen }: Props) {
  const group = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  // The board's Needs you count, this item left out.
  const others = useGroupCount("needs", item.id);
  const hs = headerState(item);
  const [pausing, setPausing] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [localCancel, setLocalCancel] = useState(false);
  const [localEscalate, setLocalEscalate] = useState(false);
  const cancelling = cancelOpen ?? localCancel;
  const setCancelling = onCancelOpen ?? setLocalCancel;
  const escalating = escalateOpen ?? localEscalate;
  const setEscalating = onEscalateOpen ?? setLocalEscalate;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ended = ENDED.has(item.display_status ?? "");
  const gone = item.worktree_exists === false;

  const run = async (p: Promise<{ ok: boolean; error?: string }>) => {
    setBusy(true);
    setError(null);
    const r = await p;
    setBusy(false);
    if (!r.ok) setError((r as { error: string }).error);
    else reload();
    return r.ok;
  };

  const draft = useDraft();
  const node = item.chain_definition.nodes.find((n) => n.id === (item.stop?.node ?? item.current_node_id));
  // Start is a resume from node zero: a never-started item has no current node.
  const start = () => void run(act.resume(item.id));
  // Start never applies a draft, so with one it asks first: Apply and start, or start without it.
  const asksFirst = () => hs.main === "start" && !!draft?.ops.some((o) => !o.passed);
  // `?start=1`: a Start pressed elsewhere (the board's peek, the phone) found a draft and
  // came here to ask the same question, once the draft is read (`startUrl`).
  const [params, setParams] = useSearchParams();
  const askedHere = params.get("start") === "1";
  const draftStatus = draft?.draft.status;
  useEffect(() => {
    if (!askedHere || !draft || draftStatus === "loading") return;
    setParams((p) => { const n = new URLSearchParams(p); n.delete("start"); return n; }, { replace: true });
    if (asksFirst()) draft.setReviewing(true, start);
    // Once per arrival: the param is gone after this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [askedHere, draftStatus]);
  const onMain = () => {
    if (hs.main === "pause") return setPausing(true);
    if (hs.main === "raise") return onSettings();
    if (asksFirst()) return draft!.setReviewing(true, start);
    if (hs.main === "resume" || hs.main === "start") return start();
    if (hs.main === "retry") return void run(act.retry(item.id, node ? { path: actionPath(node, item.stop?.task) } : {}));
    if (hs.main === "archive") return void run(act.archive(item.id));
    return void run(act.restore(item.id));
  };
  const onItem = (it: PanelItem) => {
    if (it === "escalate") setEscalating(true);
    else if (it === "complete") setCompleting(true);
    else if (it === "cancel") setCancelling(true);
    else void run(act.archive(item.id));
  };
  const duplicate = useDuplicate(item.id, setError);
  const copy = (text: string, what: string) => navigator.clipboard?.writeText(text).then(() => showToast(`Copied ${what}`), () => {});

  const menu: MenuItem[] = [
    { label: "Review changes", onSelect: () => navigate(`/work-items/${encodeURIComponent(item.id)}/review`) },
    { label: "Item settings", onSelect: onSettings },
    { label: "Open worktree in editor", onSelect: () => void run(act.openWorktree(item.id)), ...(gone && { disabled: true, sub: ended ? "worktree removed" : "worktree removed · Retry recreates it" }) },
    { label: "Copy ID", onSelect: () => copy(item.id, "ID") },
    // R21: copied links stay on the shipped path until cutover.
    { label: "Copy link", onSelect: () => copy(`${window.location.origin}/work-items/${item.id}`, "link") },
    { label: "View run log", onSelect: onRunLog },
    ...(ended
      ? [{ label: "Duplicate as new item", onSelect: duplicate }]
      : [{ label: "Escalate…", onSelect: () => setEscalating(true) }, { label: "Cancel…", onSelect: () => setCancelling(true), danger: true }]),
  ];

  const endedAt = ended ? item.updated_at : null;
  return (
    <>
    <DraftState />
    <HeaderActions>
      <ReviewButton />
      {others > 0 && (
        <Link className="item-others" to="/">
          <span className="item-dot" aria-hidden /> {others} {others === 1 ? "other needs" : "others need"} you
        </Link>
      )}
      <span className="item-elapsed" title={`Created ${new Date(item.created_at).toLocaleString()}`}>
        <Clock size={11} aria-hidden /> {elapsedBetween(item.created_at, endedAt)}
      </span>
      <span className={`item-badge is-${hs.tone}`}>{hs.badge}</span>
      <div className="item-main-group">
        <MainButton main={hs.main} panel={hs.panel} archivable={archivable(item.display_status)} busy={busy} onMain={onMain} onItem={onItem} groupRef={group} />
        <Menu label="Item menu" trigger={<EllipsisVertical size={15} aria-hidden />} items={menu} />
      </div>
      {error && !pausing && <span className="item-error item-header-error" role="alert">{error}</span>}
      <Popover anchor={group} open={pausing} onClose={() => setPausing(false)} role="dialog" label="Pause this item?">
        <PauseConfirm busy={busy} error={error} onClose={() => setPausing(false)} onPause={async () => { if (await run(act.pause(item.id))) setPausing(false); }} />
      </Popover>
      {cancelling && <CancelCard id={item.id} anchor={group} onClose={() => setCancelling(false)} onDone={() => { setCancelling(false); reload(); }} />}
      {escalating && <EscalateDialog id={item.id} onClose={() => setEscalating(false)} onDone={() => { setEscalating(false); reload(); }} />}
      {completing && <CompleteDialog id={item.id} onClose={() => setCompleting(false)} onDone={() => { setCompleting(false); reload(); }} />}
    </HeaderActions>
    </>
  );
}
