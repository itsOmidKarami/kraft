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
import { age, cancelsOwn, eventLine } from "../item/events";
import { CancelCard } from "../item/header/CancelCard";
import { EscalateCard, PauseConfirm } from "../item/header/Dialogs";
import { useDuplicate } from "../item/header/ItemHeader";
import { totals, useDiffFiles, useOpenThreads } from "../item/Top";
import { ChainConfig, ChainOverview } from "../item/panes/ChainPane";
import { actionPath, retryBody } from "../item/paths";
import { openLimitEditor } from "../item/RaiseLimit";
import { ComesAfterCard, PausedCard, StateCard } from "../item/StateCard";
import { bodyOffers, budgetRaise, headerState, MAIN_LABEL, neverStarted, offersRaise } from "../item/status";
import { placeUrl, stopPlace } from "../item/url";
import { useEvents } from "../item/useEvents";
import { useItem, type ItemDetail } from "../item/useItem";
import { Popover } from "../ui/Popover";
import { groupOf } from "./model";
import { capitalise, gateName, reasonTail } from "./rowText";
import "../item/item.css";
import { PAUSE_THIS_ITEM } from "./sharedWords";
import { REMOVE_FROM_QUEUE, REMOVE_FROM_QUEUE_TITLE } from "../item/sharedWords";
import { LinkButton } from "../ui/LinkButton";

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
  // Asked for the budget editor (a row's Raise budget) on a stop it does not raise: the stop's own
  // limit editor, or Overview, whose banner says why the item can't raise it.
  // A raise the server refuses (`offersRaise`) is Overview's too, with its Retry.
  const stop = loaded.state === "ready" ? loaded.item.stop : null;
  const canRaise = loaded.state === "ready" && offersRaise(loaded.item);
  useEffect(() => {
    if (!budget || !stop || stop.kind !== "budget" || (canRaise && budgetRaise({ stop }) === "item")) return;
    onBudget(false);
    onTab("overview");
    if (stop.limit && canRaise) openLimitEditor(id);
  }, [budget, stop, canRaise, id, onBudget, onTab]);
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
      footer={<Footer cards={tab === "overview"} item={item} reload={loaded.reload} offline={offline} onOpen={open} onRaise={raise} onAnswer={() => onTab("overview")} />}
    >
      {tab === "overview" && <Overview item={item} version={loaded.version} reload={loaded.reload} onRaise={raise} onMore={() => onTab("activity")} />}
      {tab === "activity" && <Activity id={item.id} nodes={item.chain_definition.nodes} version={loaded.version} />}
      {tab === "config" && <Config item={item} reload={loaded.reload} budget={budget} onBudget={onBudget} />}
    </Inspector>
  );
}

function Overview({ item, version, reload, onRaise, onMore }: { item: ItemDetail; version: string; reload: () => void; onRaise: () => void; onMore: () => void }) {
  const navigate = useNavigate();
  const anchor = useRef<HTMLDivElement>(null);
  const [cancelling, setCancelling] = useState(false);
  const [escalating, setEscalating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const duplicate = useDuplicate(item.id, setError);
  const events = useEvents(item.id, version);
  const openNode = (node: string) => navigate(placeUrl(item.id, stopPlace(item.stop, node, item.chain_definition.nodes)));
  return (
    <div className="peek-overview">
      <div ref={anchor} className="peek-cards">
        <Banner item={item} onRaise={onRaise} reload={reload} note={<GateNote item={item} version={version} />} />
        <StateCard item={item} reload={reload} onCancel={() => setCancelling(true)} onEscalate={() => setEscalating(true)} onDuplicate={duplicate} onOpenNode={openNode} />
        <ComesAfterCard item={item} reload={reload} />
        <PausedCard item={item} reload={reload} />
        <QuestionCard item={item} compact={false} reload={reload} onOpenThread={() => item.stop?.node && navigate(placeUrl(item.id, { node: item.stop.node, sel: { kind: "node", node: item.stop.node }, tab: "thread" }))} />
        {error && <p className="item-error" role="alert">{error}</p>}
      </div>
      <ChainOverview item={item} events={events} now={Date.now()} onSelect={openNode} onMore={onMore} where />
      {cancelling && <CancelCard id={item.id} anchor={anchor} onClose={() => setCancelling(false)} onDone={() => { setCancelling(false); reload(); }} />}
      {escalating && <EscalateCard id={item.id} anchor={anchor} threads={item.escalation_threads} onClose={() => setEscalating(false)} onDone={() => { setEscalating(false); reload(); }} />}
    </div>
  );
}

/** What the waiting gate decides on, in a line (BD-4): its place among the gates, the change and its open threads. */
function GateNote({ item, version }: { item: ItemDetail; version: string }) {
  const files = useDiffFiles(item.id, version, item.worktree_exists === false);
  const threads = useOpenThreads(item.id, version);
  const gates = item.chain_definition.nodes.filter((n) => n.kind === "gate").map((n) => n.id);
  const gate = item.pending_gate ?? item.stop?.node ?? "";
  const k = gates.indexOf(gate);
  const name = capitalise(gateName(item.chain_definition.nodes, gate));
  const place = k < 0 ? "" : k === gates.length - 1 ? `${name} is the last gate.` : `${name} is gate ${k + 1} of ${gates.length}.`;
  const { add, del } = totals(files ?? []);
  const change = files?.length ? ` ${files.length} ${files.length === 1 ? "file" : "files"}, +${add} −${del}` : "";
  const open = threads ? `${change ? "," : ""} ${threads} open ${threads === 1 ? "thread" : "threads"}` : "";
  return <>{place}{change}{open}{change || open ? "." : ""}</>;
}

const PAGE = 50;

/** Every event of the item, newest first, paged backwards (B13); a cancel's own status write is left to the cancel's line. */
function Activity({ id, nodes, version }: { id: string; nodes: ItemDetail["chain_definition"]["nodes"]; version: string }) {
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
        {events.map((e, i) => {
          // Newest first: the event before it in time is the next one down. A cancel's own status write is the cancel's line.
          if (cancelsOwn(e, events[i + 1])) return null;
          const text = eventLine(e, nodes, events[i + 1]) ?? e.type.replaceAll("_", " ");
          return (
            <li key={e.seq}>
              <span className="peek-ev-t">{age(e.created_at, now)}</span>
              {e.node_id ? <LinkButton inline onClick={() => navigate(placeUrl(id, { sel: { kind: "node", node: e.node_id! } }))}>{text}</LinkButton> : <span>{text}</span>}
            </li>
          );
        })}
      </ol>
      {more && <LinkButton className="board-more peek-earlier" onClick={() => load(events[events.length - 1].seq, events)}>Show earlier</LinkButton>}
    </div>
  );
}

function Config({ item, reload, budget, onBudget }: { item: ItemDetail; reload: () => void; budget: boolean; onBudget: (on: boolean) => void }) {
  const [policy, setPolicy] = useState<Policy | null>(null);
  useEffect(() => void api.getPolicy().then(setPolicy, () => setPolicy(null)), []);
  return <ChainConfig item={item} policy={policy} reload={reload} editBudget={budget} onEditBudget={onBudget} />;
}

/** The item's main action (W5's headerState; Start for a never-started item), then Open item. */
/** `cards`: the Overview's banner and cards are on screen, so a main action one of them carries is not repeated here (as on the item page). */
function Footer({ cards, item, reload, offline, onOpen, onRaise, onAnswer }: { cards: boolean; item: ItemDetail; reload: () => void; offline: boolean; onOpen: () => void; onRaise: () => void; onAnswer: () => void }) {
  const hs = headerState(item);
  const queued = item.display_status === "queued";
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
    if (hs.main === "retry") return void run(act.retry(item.id, retryBody(node, item.stop)));
    if (hs.main === "archive") return void run(act.archive(item.id));
    return void run(act.restore(item.id));
  };
  return (
    <>
      {/* No main when the server admits none of the item's ways on: Open item alone. A gate's door is the banner's Review. */}
      {hs.main && hs.main !== "gate" && !(cards && bodyOffers(item, hs.main)) && (
        <button ref={btn} type="button" className={`btn ${hs.main === "pause" ? "btn-secondary" : "btn-primary"}`} disabled={offline || busy} onClick={main}>
          {hs.main === "pause" ? (queued ? REMOVE_FROM_QUEUE : "‖ Pause") : MAIN_LABEL[hs.main]}
        </button>
      )}
      <button type="button" className="btn btn-secondary" onClick={onOpen}>Open item ↗</button>
      {error && !pausing && <span className="item-error peek-error" role="alert">{error}</span>}
      <Popover anchor={btn} open={pausing} notch onClose={() => setPausing(false)} role="dialog" label={queued ? REMOVE_FROM_QUEUE_TITLE : PAUSE_THIS_ITEM}>
        <PauseConfirm queued={queued} busy={busy} error={error} onClose={() => setPausing(false)} onPause={async () => { if (await run(act.pause(item.id))) setPausing(false); }} />
      </Popover>
    </>
  );
}
