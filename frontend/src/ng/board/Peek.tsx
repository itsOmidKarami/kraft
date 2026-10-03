import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as api from "../../api";
import { repoName, shortId } from "../../format";
import type { KraftEvent, Policy } from "../../types";
import { Inspector } from "../graph/Inspector";
import type { useResizable } from "../graph/useResizable";
import { request } from "../http";
import { act, askStartUrl, draftWaits, type Done } from "../item/actions";
import { Banner, QuestionCard } from "../item/Banner";
import { age, eventLine } from "../item/events";
import { CancelCard } from "../item/header/CancelCard";
import { EscalateCard, PauseConfirm } from "../item/header/Dialogs";
import { useDuplicate } from "../item/header/ItemHeader";
import { useSelect } from "../item/draft/select";
import { ChainConfig, ChainOverview } from "../item/panes/ChainPane";
import { actionPath } from "../item/paths";
import { openLimitEditor } from "../item/RaiseLimit";
import { PausedCard, StateCard } from "../item/StateCard";
import { headerState, MAIN_LABEL, neverStarted } from "../item/status";
import { placeUrl } from "../item/url";
import { useEvents } from "../item/useEvents";
import { useItem, type ItemDetail } from "../item/useItem";
import { Popover } from "../ui/Popover";
import { groupOf } from "./model";
import { reasonTail } from "./rowText";
import "../item/item.css";

export type PeekTab = "overview" | "activity" | "config";

const GROUP_WORD = { needs: "Needs you", running: "Running", not_started: "Not started", done: "Done" } as const;

/** The board's docked pane (K3, W6 brief E): the item's card, its chain
 *  summary, its events and its settings, by import from the item page. */
export function Peek({ id, tab, onTab, budget, onBudget, offline, size, onClose, onRepo }: {
  id: string;
  tab: PeekTab;
  onTab: (t: PeekTab) => void;
  /** The budget editor open on Config (a budget stop's Raise budget). */
  budget: boolean;
  onBudget: (on: boolean) => void;
  offline: boolean;
  size: ReturnType<typeof useResizable>;
  /** The Board crumb, Escape and the collapse button close it: on the board the peek has no rail. */
  onClose: () => void;
  onRepo: (repo: string) => void;
}) {
  const loaded = useItem(id);
  const navigate = useNavigate();
  const open = () => navigate(`/work-items/${encodeURIComponent(id)}`);
  const common = { id: "board-peek", open: true, size, onCollapse: onClose, onExpand: () => {} };
  if (loaded.state !== "ready")
    return (
      <Inspector {...common} crumbs={[{ label: "Board", onClick: onClose }]} icon="workflow" title={shortId(id)}>
        <p className="item-muted">{loaded.state === "loading" ? "Loading…" : "This item is gone."}</p>
      </Inspector>
    );
  const item = loaded.item;
  // Raise cap / Raise budget: the banner's editor for a stop that names its limit (R12b-06),
  // else the Config tab, its budget editor open for a budget stop.
  const raise = () => {
    if (item.stop?.limit) return void (onTab("overview"), openLimitEditor(item.id));
    onTab("config");
    onBudget(item.stop?.kind === "budget");
  };
  const fresh = neverStarted(item);
  return (
    <Inspector
      {...common}
      crumbs={[{ label: "Board", onClick: onClose }, { label: repoName(item.repo), onClick: () => onRepo(item.repo) }]}
      icon="workflow"
      // The item's title heads the peek and names its landmark; the id follows in the line under it (R7b-16).
      title={item.title}
      prose
      // A waiting item sits in the board's Running group, but it is waiting, not running (R11b-05).
      sub={`${item.bead_id || shortId(item.id)} · ${fresh ? "Not started" : item.display_status === "waiting" ? "Waiting" : GROUP_WORD[groupOf(item)]} · ${reasonTail(item)}`}
      tabs={[{ value: "overview", label: "Overview" }, { value: "activity", label: "Activity" }, { value: "config", label: "Config" }]}
      tab={tab}
      onTab={(t) => onTab(t as PeekTab)}
      onFocus={open}
      footer={<Footer item={item} reload={loaded.reload} offline={offline} onOpen={open} onRaise={raise} onAnswer={() => onTab("overview")} />}
    >
      {tab === "overview" && <Overview item={item} version={loaded.version} reload={loaded.reload} onRaise={raise} />}
      {tab === "activity" && <Activity id={item.id} version={loaded.version} />}
      {tab === "config" && <Config item={item} reload={loaded.reload} budget={budget} onBudget={onBudget} />}
    </Inspector>
  );
}

function Overview({ item, version, reload, onRaise }: { item: ItemDetail; version: string; reload: () => void; onRaise: () => void }) {
  const navigate = useNavigate();
  const anchor = useRef<HTMLDivElement>(null);
  const [cancelling, setCancelling] = useState(false);
  const [escalating, setEscalating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const duplicate = useDuplicate(item.id, setError);
  const events = useEvents(item.id, version);
  const openNode = (node: string) => navigate(placeUrl(item.id, { sel: { kind: "node", node } }));
  const openGate = useSelect(item.id);
  return (
    <div className="peek-overview">
      <div ref={anchor} className="peek-cards">
        <Banner item={item} onOpenGate={openGate} onRaise={onRaise} reload={reload} />
        <StateCard item={item} reload={reload} onCancel={() => setCancelling(true)} onEscalate={() => setEscalating(true)} onDuplicate={duplicate} onOpenNode={openNode} />
        <PausedCard item={item} reload={reload} />
        <QuestionCard item={item} compact={false} reload={reload} onOpenThread={() => item.stop?.node && navigate(placeUrl(item.id, { node: item.stop.node, sel: { kind: "node", node: item.stop.node }, tab: "thread" }))} />
        {error && <p className="item-error" role="alert">{error}</p>}
      </div>
      <ChainOverview item={item} events={events} now={Date.now()} onSelect={openNode} />
      {cancelling && <CancelCard id={item.id} anchor={anchor} onClose={() => setCancelling(false)} onDone={() => { setCancelling(false); reload(); }} />}
      {escalating && <EscalateCard id={item.id} anchor={anchor} onClose={() => setEscalating(false)} onDone={() => { setEscalating(false); reload(); }} />}
    </div>
  );
}

const PAGE = 50;

/** Every event of the item, newest first, paged backwards (B13). */
function Activity({ id, version }: { id: string; version: string }) {
  const navigate = useNavigate();
  const [events, setEvents] = useState<KraftEvent[]>([]);
  const [more, setMore] = useState(false);
  const load = async (before: number, keep: KraftEvent[]) => {
    const r = await request<KraftEvent[]>(`/work-items/${encodeURIComponent(id)}/events?before_seq=${before}&limit=${PAGE}`);
    if (r.status !== 200 || !Array.isArray(r.body)) return;
    setEvents([...keep, ...[...r.body].reverse()]);
    setMore(r.body.length === PAGE);
  };
  useEffect(() => void load(2 ** 31 - 1, []), [id, version]); // eslint-disable-line react-hooks/exhaustive-deps
  const now = Date.now();
  return (
    <div className="peek-activity">
      <ol className="peek-events">
        {events.map((e) => {
          const text = eventLine(e) ?? e.type.replaceAll("_", " ");
          return (
            <li key={e.seq}>
              <span className="peek-ev-t">{age(e.created_at, now)}</span>
              {e.node_id ? <button type="button" className="peek-ev-link" onClick={() => navigate(placeUrl(id, { sel: { kind: "node", node: e.node_id! } }))}>{text}</button> : <span>{text}</span>}
            </li>
          );
        })}
      </ol>
      {more && <button type="button" className="board-more peek-earlier" onClick={() => load(events[events.length - 1].seq, events)}>Show earlier</button>}
    </div>
  );
}

function Config({ item, reload, budget, onBudget }: { item: ItemDetail; reload: () => void; budget: boolean; onBudget: (on: boolean) => void }) {
  const [policy, setPolicy] = useState<Policy | null>(null);
  useEffect(() => void api.getPolicy().then(setPolicy, () => setPolicy(null)), []);
  return <ChainConfig item={item} policy={policy} reload={reload} editBudget={budget} onEditBudget={onBudget} />;
}

/** The item's main action (W5's headerState; Start for a never-started item), then Open item. */
function Footer({ item, reload, offline, onOpen, onRaise, onAnswer }: { item: ItemDetail; reload: () => void; offline: boolean; onOpen: () => void; onRaise: () => void; onAnswer: () => void }) {
  const hs = headerState(item);
  const btn = useRef<HTMLButtonElement>(null);
  const [pausing, setPausing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (p: Promise<Done>) => {
    setBusy(true);
    setError(null);
    const r = await p;
    setBusy(false);
    if (!r.ok) setError(r.error);
    else reload();
    return r.ok;
  };
  const node = item.chain_definition.nodes.find((n) => n.id === (item.stop?.node ?? item.current_node_id));
  const navigate = useNavigate();
  const openGate = useSelect(item.id);
  const startHere = async () => {
    setBusy(true);
    setError(null);
    const waits = await draftWaits(item.id);
    setBusy(false);
    // The item page asks what its header's Start asks: Apply and start, or Start without them.
    if (waits) return navigate(askStartUrl(item.id));
    await run(act.resume(item.id));
  };
  const main = () => {
    if (hs.main === "pause") return setPausing(true);
    if (hs.main === "raise") return onRaise();
    if (hs.main === "gate") return openGate(item.pending_gate ?? item.stop?.node ?? null);
    // The question card is on Overview, its answer box with it.
    if (hs.main === "answer") {
      onAnswer();
      return requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('.pane textarea[aria-label="Your answer"]')?.focus());
    }
    if (hs.main === "conflicts") return navigate(`/work-items/${encodeURIComponent(item.id)}/review${item.stop?.node ? `?nodes=${encodeURIComponent(item.stop.node)}` : ""}`);
    if (hs.main === "reopen") return void run(act.reopenMr(item.id));
    if (hs.main === "resume") return void run(act.resume(item.id));
    // Start never applies a draft: with one, the item page asks first (R9b-01's other door).
    if (hs.main === "start") return void startHere();
    if (hs.main === "retry") return void run(act.retry(item.id, node ? { path: actionPath(node, item.stop?.task) } : {}));
    if (hs.main === "archive") return void run(act.archive(item.id));
    return void run(act.restore(item.id));
  };
  return (
    <>
      <button ref={btn} type="button" className={`btn ${hs.main === "pause" ? "btn-secondary" : "btn-primary"}`} disabled={offline || busy} onClick={main}>
        {hs.main === "pause" ? "‖ Pause" : MAIN_LABEL[hs.main]}
      </button>
      <button type="button" className="btn btn-secondary" onClick={onOpen}>Open item ↗</button>
      {error && !pausing && <span className="item-error peek-error" role="alert">{error}</span>}
      <Popover anchor={btn} open={pausing} notch onClose={() => setPausing(false)} role="dialog" label="Pause this item?">
        <PauseConfirm busy={busy} error={error} onClose={() => setPausing(false)} onPause={async () => { if (await run(act.pause(item.id))) setPausing(false); }} />
      </Popover>
    </>
  );
}
