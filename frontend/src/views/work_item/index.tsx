import { useEffect, useState, type WheelEvent } from "react";
import { useLocation, useParams } from "react-router-dom";
import * as api from "../../api";
import { Row, RowState, RowText, StatusGlyph } from "../../components/ui";
import { until } from "../../format";
import { useStore } from "../../store";
import type { SessionStatus, WorkItemDiff } from "../../types";
import { ActionBar } from "./ActionBar";
import { GraphSplit } from "./GraphSplit";
import { Header } from "./Header";
import { Inspector } from "./Inspector";
import { PhoneNode, PhoneStageList, PhoneTopBar } from "./Phone";
import { Log } from "./RightPane/Log";
import { RightPane } from "./RightPane";
import { StageGraph } from "./StageGraph";
import { useItemUrlState } from "./useItemUrlState";
import { usePhone } from "./usePhone";
import "./work_item.css";

/**
 * The item page (UI v2 · 05, desktop 11–16 + the body of 21). Replaces the
 * 670-line `WorkItemDetail.tsx` (Kraft-mkyq): this file only orchestrates —
 * header, action bar, stage graph, split, inspector and right pane are each
 * their own module.
 */

/** repos-panel state → the glyph/label vocabulary session rows already use. */
function repoGlyphStatus(state: string): SessionStatus {
  if (state === "merged") return "done";
  if (state === "failed") return "failed";
  if (state === "open") return "running";
  return "pending";
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

  const { nodeId, nodeExplicit, tab, selection, maximized, selectNode, setTab, select, setMaximized, goTo, goToNode, reviewHref } =
    useItemUrlState(item?.current_node_id ?? null);
  const location = useLocation();

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

  if (loadErr && !item) {
    return (
      <p className="empty" role="alert">
        could not load this work item — {loadErr}
      </p>
    );
  }
  if (!item) return <p className="empty">unknown work item</p>;

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
        />
      </div>
    );
  }

  // The phone list's own log summary is always the Tasks tab's session,
  // regardless of which tab the hash currently names — read that key
  // directly rather than through `selection`, which only ever reflects the
  // active tab.
  const currentLogSessionId = new URLSearchParams(location.hash.replace(/^#/, "")).get("session");

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
            {item.progress && ` · Task ${item.progress.current} of ${item.progress.total}`}
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
            maximized
            onToggleMaximize={() => setMaximized(false)}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="detail item-page" data-head={headCollapsed ? "collapsed" : undefined}>
      {phone && <PhoneTopBar item={item} />}
      <Header
        item={item}
        events={events}
        collapsed={headCollapsed}
        onWheel={onHeadWheel}
        onExpand={() => setHeadCollapsed(false)}
      />

      {!!item.repos?.length && (
        <section className="repos-panel">
          <div className="repos-head">
            <span className="section-label">Repos</span>
            <span>merge rank · deepest first</span>
            <span className="repos-policy">
              root_merge_policy: <b>{item.root_merge_policy}</b>
            </span>
          </div>
          {item.repos.map((r) => (
            <Row key={r.path} columns="22px 1fr 100px auto" data-repo={r.path}>
              <StatusGlyph status={repoGlyphStatus(r.state)} />
              <RowText
                title={r.repo}
                sub={
                  <>
                    <code>{r.path}</code> · merge rank {r.merge_rank}
                  </>
                }
              />
              <span className="row-sub">{r.role}</span>
              <RowState status={repoGlyphStatus(r.state)}>{r.state}</RowState>
            </Row>
          ))}
        </section>
      )}

      <ActionBar
        item={item}
        sessions={sessions}
        events={events}
        onReviewChanges={goToChanges}
        onEditChain={goToConfig}
        reviewHref={reviewHref}
      />

      {phone ? (
        <>
          <PhoneStageList item={item} events={events} onSelect={selectNode} />
          {currentLogSessionId && (
            <div className="phone-node-log">
              <p className="section-label">Log · {item.current_node_id}</p>
              <Log sessionId={currentLogSessionId} capLines={8} />
            </div>
          )}
        </>
      ) : (
        <GraphSplit
          graph={<StageGraph item={item} selected={nodeId} onSelect={selectNode} />}
          lower={
            <div className="item-split">
              <Inspector
                item={item}
                sessions={sessions}
                events={events}
                nodeId={nodeId}
                tab={tab}
                onTabChange={setTab}
                selection={selection}
                onSelect={select}
                diff={diff}
                diffError={diffError}
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
                  maximized={false}
                  onToggleMaximize={() => setMaximized(true)}
                />
              </div>
            </div>
          }
        />
      )}

      {item.retry_at && (item.status === "rate_limited" || item.status === "waiting") && (
        <p className="control-hint">next check {until(item.retry_at)}</p>
      )}
    </div>
  );
}
