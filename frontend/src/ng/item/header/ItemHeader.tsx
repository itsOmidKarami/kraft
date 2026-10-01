import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { elapsedBetween } from "../../../format";
import { useStore } from "../../../store";
import { Clock, EllipsisVertical } from "../../icons";
import { legacyPath } from "../../legacyPath";
import { HeaderActions } from "../../shell/HeaderActions";
import { Menu, type MenuItem } from "../../ui/Menu";
import { Popover } from "../../ui/Popover";
import { showToast } from "../../ui/Toast";
import { act } from "../actions";
import { actionPath } from "../paths";
import { archivable, headerState, type PanelItem } from "../status";
import type { ItemDetail } from "../useItem";
import { CancelCard } from "./CancelCard";
import { CompleteDialog, EscalateDialog, PauseConfirm } from "./Dialogs";
import { MainButton } from "./MainButton";

const ENDED = new Set(["done", "cancelled", "archived"]);

/** The shipped page for an /ng path (review, the board): a full load until its wave lands. */
export const goShipped = (ngPath: string) => window.location.assign(legacyPath({ pathname: ngPath, search: "" }));

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
  const navigate = useNavigate();
  const group = useRef<HTMLDivElement>(null);
  const others = useStore((s) => Object.values(s.workItems).filter((w) => w.display_status === "needs_you" && w.id !== item.id).length);
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

  const run = async (p: Promise<{ ok: boolean; error?: string }>) => {
    setBusy(true);
    setError(null);
    const r = await p;
    setBusy(false);
    if (!r.ok) setError((r as { error: string }).error);
    else reload();
    return r.ok;
  };

  const node = item.chain_definition.nodes.find((n) => n.id === (item.stop?.node ?? item.current_node_id));
  const onMain = () => {
    if (hs.main === "pause") return setPausing(true);
    if (hs.main === "raise") return onSettings();
    if (hs.main === "resume") return void run(act.resume(item.id));
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
  const duplicate = async () => {
    const r = await act.duplicate(item.id);
    if (!r.ok) return setError(r.error);
    if (r.body.duplicate_warning) showToast(r.body.duplicate_warning, 6000);
    navigate(`/work-items/${encodeURIComponent(r.body.id)}`);
  };
  const copy = (text: string, what: string) => navigator.clipboard?.writeText(text).then(() => showToast(`Copied ${what}`), () => {});

  const menu: MenuItem[] = [
    { label: "Review changes", onSelect: () => goShipped(`/ng/work-items/${item.id}/review`) },
    { label: "Item settings", onSelect: onSettings },
    { label: "Open worktree in editor", onSelect: () => void run(act.openWorktree(item.id)) },
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
    <HeaderActions>
      {others > 0 && (
        <a className="item-others" href="/">
          <span className="item-dot" aria-hidden /> {others} {others === 1 ? "other needs" : "others need"} you
        </a>
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
  );
}
