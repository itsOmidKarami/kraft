import { useEffect, useLayoutEffect, useRef, useState, type RefObject, type WheelEvent } from "react";
import { useLocation, useParams } from "react-router-dom";
import * as api from "../../api";
import { until } from "../../format";
import { useStore } from "../../store";
import type { WorkItemDiff } from "../../types";
import { ItemCard } from "./ActionBar/ItemCard";
import { GraphSplit } from "./GraphSplit";
import { deriveState } from "../../deriveState";
import { Header } from "./Header";
import { Inspector } from "./Inspector";
import { ChainDescription, NotStartedCard } from "./NotStarted";
import { PhoneNode, PhoneStageList, PhoneTopBar } from "./Phone";
import { Log } from "./RightPane/Log";
import { RightPane } from "./RightPane";
import { StageGraph } from "./StageGraph";
import { Segmented } from "../../components/ui";
import type { InspectorTab, Selection } from "./selection";
import { useItemUrlState } from "./useItemUrlState";
import { useMedia, usePhone } from "./usePhone";
import "./work_item.css";

/**
 * The item page (UI v2 · 05, desktop 11–16 + the body of 21). Replaces the
 * 670-line `WorkItemDetail.tsx` (Kraft-mkyq): this file only orchestrates —
 * header, action bar, stage graph, split, inspector and right pane are each
 * their own module.
 */

/** W0.2: the split never goes under 320px. When the fixed block (header,
 *  gate card, action bar, graph) leaves less than that, it gives way in
 *  steps — gate-card notes to one line, then the description to one line,
 *  then the graph to its 48px minimum — each a `data-fit` value the CSS
 *  reads; no pixel heights are set from here. Re-derived from the loosest
 *  step on every resize, so a closed composer or a taller window relaxes it.
 *  Watching `.graph-split` catches every change above it: the page's height
 *  is fixed, so anything that grows the fixed block shrinks the split. */
const FIT_STEPS = ["tight", "tighter", "tightest"] as const;
const SPLIT_FLOOR = 320;

function useSplitFit(ref: RefObject<HTMLDivElement>, enabled: boolean, key: string | undefined) {
  useLayoutEffect(() => {
    const page = ref.current;
    if (!enabled || !page || typeof ResizeObserver === "undefined") return;
    let frame = 0;
    const fit = () => {
      frame = 0;
      const lower = page.querySelector<HTMLElement>(".graph-split-lower");
      if (!lower) return;
      const room = () =>
        page.getBoundingClientRect().bottom -
        parseFloat(getComputedStyle(page).paddingBottom) -
        lower.getBoundingClientRect().top;
      delete page.dataset.fit;
      for (const step of FIT_STEPS) {
        if (room() >= SPLIT_FLOOR) break;
        page.dataset.fit = step;
      }
    };
    const ro = new ResizeObserver(() => {
      if (!frame) frame = requestAnimationFrame(fit);
    });
    ro.observe(page);
    const split = page.querySelector(".graph-split");
    if (split) ro.observe(split);
    return () => {
      ro.disconnect();
      cancelAnimationFrame(frame);
      delete page.dataset.fit;
    };
  }, [ref, enabled, key]);
}

export function WorkItemDetail() {
  const { id = "" } = useParams();
  const item = useStore((s) => s.workItems[id]);
  const sessions = useStore((s) => s.sessionsByItem[id] ?? []);
  const events = useStore((s) => s.eventsByItem[id] ?? []);
  const hydrateItem = useStore((s) => s.hydrateItem);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [diff, setDiff] = useState<WorkItemDiff | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  const phone = usePhone();

  // Kraft-yx8v: a wheel over the top block collapses the title/description,
  // latched with a threshold so trackpad momentum can't flip-flop the state.
  // Wheel, not scroll: WI-3 made this page `overflow: hidden` and the top
  // block is not itself a scroller, so there is no scroll event to listen for.
  const [headCollapsed, setHeadCollapsed] = useState(false);
  const onHeadWheel = (e: WheelEvent<HTMLDivElement>) => {
    if (e.deltaY > 24 && !headCollapsed) setHeadCollapsed(true);
    else if (e.deltaY < -24 && headCollapsed) setHeadCollapsed(false);
  };

  // A finished item has no current node; select the one it finished on, so
  // the inspector names a node and Tasks counts that node's sessions (W0.5, W0.8).
  const finished = !!item && (item.status === "completed" || item.status === "abandoned" || !!item.archived_at);
  const { nodeId, nodeExplicit, tab, selection, maximized, selectNode, setTab, select, setMaximized, goTo, goToNode, reviewHref } =
    useItemUrlState(item?.current_node_id ?? (finished ? (item.chain_definition.nodes.at(-1)?.id ?? null) : null));
  const location = useLocation();

  // Tablet (768–1023, W2.2): one pane under the tab strip — the list, or the
  // selected row's detail. Picking a row shows its detail; a new tab starts
  // on its list.
  const tablet = useMedia("(max-width: 1023px)") && !phone;
  const [paneView, setPaneView] = useState<"list" | "detail">("list");
  const pickRow = (s: Selection) => {
    select(s);
    if (tablet) setPaneView("detail");
  };
  const pickTab = (t: InspectorTab) => {
    setTab(t);
    if (tablet) setPaneView("list");
  };

  const pageRef = useRef<HTMLDivElement>(null);
  useSplitFit(pageRef, !!item && !phone && !maximized, item?.id);

  // The hero's "+N submodules" chip (W0.7): switch to Config, then scroll the
  // inspector — not the window — so the Repos section sits under its tabs.
  // Keyed on `tab` too, since the Config tab renders a commit after setTab.
  const [reposFocus, setReposFocus] = useState(0);
  useEffect(() => {
    if (!reposFocus) return;
    const target = document.getElementById("config-repos");
    const scroller = target?.closest<HTMLElement>(".inspector");
    if (!target || !scroller) return;
    const under = scroller.querySelector(".tabs")?.getBoundingClientRect().bottom ?? scroller.getBoundingClientRect().top;
    scroller.scrollTop += target.getBoundingClientRect().top - under;
    setReposFocus(0);
  }, [reposFocus, tab]);

  useEffect(() => {
    if (!maximized) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMaximized(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [maximized]);

  useEffect(() => {
    const load = () =>
      hydrateItem(id).then(
        () => setLoadErr(null),
        (e) => setLoadErr(e instanceof Error ? e.message : String(e)),
      );
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [id, hydrateItem]);

  // One fetch for the whole page: `Inspector/Changes.tsx`'s tree and
  // `RightPane/Diff.tsx` used to each call `getWorkItemDiff` on their own —
  // two-way sync between them needs both sides looking at the same list.
  useEffect(() => {
    let alive = true;
    api
      .getWorkItemDiff(id)
      .then((d) => alive && setDiff(d))
      .catch((e) => alive && setDiffError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [id]);

  // Defaulting the Tasks selection to the selected node's newest session, so
  // the log pane is never blank the moment a tab opens. Only while the Tasks
  // tab is the one actually showing: `select` writes whichever key the
  // *current* tab owns, so firing this from another tab would clobber that
  // tab's own selection.
  useEffect(() => {
    if (tab !== "tasks" || selection.id) return;
    const latest = [...sessions]
      .filter((s) => s.node_id === nodeId)
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
    if (latest) select({ kind: "session", id: latest.id });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, nodeId, sessions.length]);

  const goToLog = (sessionId: string) => goTo("tasks", sessionId);
  // On the phone list (m04) "Review changes" has no split to switch a tab
  // in — it has to open the node page (m05) on the Changes tab instead.
  const goToChanges = () => {
    if (phone && item?.current_node_id) goToNode("changes", item.current_node_id);
    else setTab("changes");
  };
  // The not_started bar's "Edit chain" (06): opens the Config tab.
  const goToConfig = () => {
    if (phone && item?.current_node_id) goToNode("config", item.current_node_id);
    else setTab("config");
  };
  const showRepos = () => {
    if (phone) return goToConfig();
    setTab("config");
    setReposFocus((n) => n + 1);
  };

  if (loadErr && !item) {
    return (
      <p className="empty" role="alert">
        could not load this work item — {loadErr}
      </p>
    );
  }
  if (!item) return <p className="empty">unknown work item</p>;

  // Maximize is a page layout, not the pane's own state (43): the header,
  // repos panel, action bar, graph and inspector all give way to a 36px
  // strip, and the right pane gets the rest.
  if (maximized) {
    return (
      <div className="detail item-page" data-maximized="true">
        {phone && <PhoneTopBar item={item} />}
        <div className="item-max-strip">
          <span className="item-max-title">{item.title}</span>
          <span className="item-max-node">
            {item.current_node_id}
            {item.progress &&
              ` · ${item.progress.current} of ${item.progress.total} · ${item.progress.title}`}
          </span>
          <span className="item-max-actions">
            <button className="btn btn-ghost" onClick={() => setMaximized(false)}>
              Restore
            </button>
          </span>
        </div>
        <div className="item-right-pane" data-maximized="true">
          <RightPane
            item={item}
            events={events}
            sessions={sessions}
            nodeId={nodeId}
            tab={tab}
            selection={selection}
            diff={diff}
            diffError={diffError}
            onViewLog={goToLog}
            onTimelineSelect={(id) => select({ kind: "timeline-node", id })}
            maximized
            onToggleMaximize={() => setMaximized(false)}
          />
        </div>
      </div>
    );
  }

  // m05: tapping a stage on the phone list opens this full-screen instead
  // of the desktop split — a real hash-selected node, not just the item's
  // current one (`nodeExplicit`, from `useItemUrlState` above).
  if (phone && nodeExplicit && nodeId) {
    return (
      <div className="detail item-page">
        <PhoneNode
          item={item}
          sessions={sessions}
          events={events}
          nodeId={nodeId}
          tab={tab}
          onTabChange={setTab}
          selection={selection}
          onSelect={select}
          onToggleMaximize={() => setMaximized(true)}
        />
      </div>
    );
  }

  // The phone list's own log summary is always the Tasks tab's session,
  // regardless of which tab the hash currently names — read that key
  // directly rather than through `selection`, which only ever reflects the
  // active tab.
  const currentLogSessionId = new URLSearchParams(location.hash.replace(/^#/, "")).get("session");

  return (
    <div className="detail item-page" ref={pageRef} data-head={headCollapsed ? "collapsed" : undefined}>
      {phone && <PhoneTopBar item={item} />}
      <Header
        item={item}
        events={events}
        sessions={sessions}
        collapsed={headCollapsed}
        onWheel={onHeadWheel}
        onExpand={() => setHeadCollapsed(false)}
        onShowRepos={showRepos}
      />

      <ItemCard
        item={item}
        sessions={sessions}
        events={events}
        onReviewChanges={goToChanges}
        onEditChain={goToConfig}
        reviewHref={reviewHref}
      />

      {phone ? (
        <>
          <PhoneStageList item={item} events={events} sessions={sessions} onSelect={selectNode} />
          {currentLogSessionId && (
            <div className="phone-node-log">
              <p className="section-label">Log · {item.current_node_id}</p>
              <Log sessionId={currentLogSessionId} capLines={8} />
            </div>
          )}
        </>
      ) : (
        <GraphSplit
          graph={<StageGraph item={item} events={events} sessions={sessions} selected={nodeId} onSelect={selectNode} />}
          lower={
            // Spec 21 (Kraft-pfqdb): nothing has run, so the default view is
            // the intake card and the chain it will walk. "Edit chain" (Config)
            // or any other tab still opens the normal split.
            deriveState(item, sessions, events).state === "not_started" && tab === "tasks" ? (
              <div className="item-split not-started-split">
                <NotStartedCard item={item} />
                <div className="item-right-pane">
                  <ChainDescription item={item} />
                </div>
              </div>
            ) : (
            <div className="item-split" data-view={tablet ? paneView : undefined}>
              <Inspector
                item={item}
                sessions={sessions}
                events={events}
                nodeId={nodeId}
                tab={tab}
                onTabChange={pickTab}
                selection={selection}
                onSelect={pickRow}
                diff={diff}
                diffError={diffError}
                headExtra={
                  tablet ? (
                    <Segmented
                      options={[
                        { id: "list", label: "List" },
                        { id: "detail", label: "Detail" },
                      ]}
                      value={paneView}
                      onChange={setPaneView}
                    />
                  ) : undefined
                }
              />
              <div className="item-right-pane">
                <RightPane
                  item={item}
                  events={events}
                  sessions={sessions}
                  nodeId={nodeId}
                  tab={tab}
                  selection={selection}
                  diff={diff}
                  diffError={diffError}
                  onViewLog={goToLog}
                  onTimelineSelect={(id) => select({ kind: "timeline-node", id })}
                  maximized={false}
                  onToggleMaximize={() => setMaximized(true)}
                />
              </div>
            </div>
            )
          }
        />
      )}

      {item.retry_at && (item.status === "rate_limited" || item.status === "waiting") && (
        <p className="control-hint">next check {until(item.retry_at)}</p>
      )}
    </div>
  );
}
