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
import { archivable, headerState, menuDoors, type PanelItem } from "../status";
import type { ItemDetail } from "../useItem";
import { CancelCard } from "./CancelCard";
import { CompleteCard, EscalateCard, PauseConfirm } from "./Dialogs";
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
  /** Select the chain and open its Config tab (Item settings). */
  onSettings: () => void;
  /** Raise cap: the Config tab with the budget editor open on a budget stop. Item settings when absent. */
  onRaise?: () => void;
  /** Open gate: the gate's pane on the item page. The review page's gate view when absent. */
  onGate?: (gate: string) => void;
  /** Answer: the question card's answer box. The item page when absent. */
  onAnswer?: () => void;
  /** Outside triggers for the cancel card (the MR-closed state card's Cancel item…). */
  cancelOpen?: boolean;
  onCancelOpen?: (open: boolean) => void;
  escalateOpen?: boolean;
  onEscalateOpen?: (open: boolean) => void;
};

/** The right side of the item page's header row (Decisions §1, §14): others
 *  need you, elapsed, the badge, the main button with its panel, and ⋮. */
export function ItemHeader({ item, reload, onSettings, onRaise, onGate, onAnswer, cancelOpen, onCancelOpen, escalateOpen, onEscalateOpen }: Props) {
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
  // The elapsed clock ticks as the pane's does: every second while an agent or a check runs, else every 30 s (R11b-11).
  const ticking = !ended && item.worker_sessions.some((s) => s.status === "running" || s.status === "pending");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), ticking ? 1_000 : 30_000);
    return () => clearInterval(t);
  }, [ticking]);

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
  // The draft this page read can be stale: one made in another tab or from the CLI since
  // would be started past. Start reads it again first, as the peek and the phone do (R10b-07).
  const startFresh = async () => {
    if (!draft) return start();
    setBusy(true);
    const a = await draft.draft.reload();
    setBusy(false);
    if (a.status === 404) return start();
    const ops = a.status === 200 ? (a.body as { ops?: { passed?: boolean }[] }).ops : undefined;
    if (!Array.isArray(ops)) return setError("Could not read this item's draft, so it was not started: try again.");
    if (ops.some((o) => !o.passed)) return draft.setReviewing(true, start);
    start();
  };
  // Once a card's action is done the card is gone, and the button in it that had the
  // focus with it: the focus goes to the main button, which is the next way on (R12b-08).
  const toMain = () => requestAnimationFrame(() => group.current?.querySelector<HTMLElement>(".item-main-action")?.focus());
  const itemUrl = `/work-items/${encodeURIComponent(item.id)}`;
  const onMain = () => {
    if (hs.main === "pause") return setPausing(true);
    if (hs.main === "raise") return (onRaise ?? onSettings)();
    if (hs.main === "gate") {
      const gate = item.pending_gate ?? item.stop?.node ?? "";
      return onGate ? onGate(gate) : navigate(`${itemUrl}/review?gate=${encodeURIComponent(gate)}`);
    }
    if (hs.main === "answer") return onAnswer ? onAnswer() : navigate(itemUrl);
    if (hs.main === "conflicts") return navigate(`${itemUrl}/review${item.stop?.node ? `?nodes=${encodeURIComponent(item.stop.node)}` : ""}`);
    if (hs.main === "reopen") return void run(act.reopenMr(item.id));
    if (asksFirst()) return draft!.setReviewing(true, start);
    if (hs.main === "start") return void startFresh();
    if (hs.main === "resume") return start();
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
    // Escalate… only where /escalate takes it: not on a running item, nor while a turn runs.
    ...menuDoors(item).map((d): MenuItem => (d === "duplicate" ? { label: "Duplicate as new item", onSelect: duplicate }
      : d === "escalate" ? { label: "Escalate…", onSelect: () => setEscalating(true) }
      : { label: "Cancel…", onSelect: () => setCancelling(true), danger: true })),
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
        <Clock size={11} aria-hidden /> {elapsedBetween(item.created_at, endedAt, now)}
      </span>
      <span className={`item-badge is-${hs.tone}`}>{hs.badge}</span>
      <div className="item-main-group">
        <MainButton main={hs.main} panel={hs.panel} archivable={archivable(item.display_status)} busy={busy} onMain={onMain} onItem={onItem} groupRef={group} />
        <Menu label="Item menu" trigger={<EllipsisVertical size={15} aria-hidden />} items={menu} />
      </div>
      {error && !pausing && <span className="item-error item-header-error" role="alert">{error}</span>}
      <Popover anchor={group} open={pausing} notch onClose={() => setPausing(false)} role="dialog" label="Pause this item?">
        <PauseConfirm busy={busy} error={error} onClose={() => setPausing(false)} onPause={async () => { if (await run(act.pause(item.id))) { setPausing(false); toMain(); } }} />
      </Popover>
      {cancelling && <CancelCard id={item.id} anchor={group} onClose={() => setCancelling(false)} onDone={() => { setCancelling(false); reload(); toMain(); }} />}
      {escalating && <EscalateCard id={item.id} anchor={group} onClose={() => setEscalating(false)} onDone={() => { setEscalating(false); reload(); toMain(); }} />}
      {completing && <CompleteCard id={item.id} anchor={group} onClose={() => setCompleting(false)} onDone={() => { setCompleting(false); reload(); toMain(); }} />}
    </HeaderActions>
    </>
  );
}
