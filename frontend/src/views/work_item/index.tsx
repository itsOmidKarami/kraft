import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Row, RowState, RowText, StatusGlyph } from "../../components/ui";
import { until } from "../../format";
import { useStore } from "../../store";
import type { SessionStatus } from "../../types";
import { ActionBar } from "./ActionBar";
import { GraphSplit } from "./GraphSplit";
import { Header } from "./Header";
import { Inspector } from "./Inspector";
import { PhoneNode, PhoneStageList, PhoneTopBar } from "./Phone";
import { Log } from "./RightPane/Log";
import { RightPane } from "./RightPane";
import { StageGraph } from "./StageGraph";
import { EMPTY_SELECTION, type InspectorTab, type Selection } from "./selection";
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

/** The selected node id, backed by the URL hash `#node=<id>` (common rules:
 *  "the selected node is the URL hash"). `useLocation`/`useNavigate` keep
 *  this a normal history entry, so back/forward moves between selections.
 *  The third element is whether the hash itself named a node, as opposed to
 *  `defaultNodeId` filling in for an empty hash — the phone page (m05) only
 *  opens full-screen once a stage was actually tapped, not just because the
 *  item has a current node. */
function useNodeSelection(defaultNodeId: string | null): [string | null, (id: string) => void, boolean] {
  const location = useLocation();
  const navigate = useNavigate();
  const match = /(?:^|#)node=([^&]+)/.exec(location.hash);
  const fromHash = match ? decodeURIComponent(match[1]) : null;
  const select = (id: string) => navigate({ hash: `node=${encodeURIComponent(id)}` });
  return [fromHash ?? defaultNodeId, select, fromHash != null];
}

export function WorkItemDetail() {
  const { id = "" } = useParams();
  const item = useStore((s) => s.workItems[id]);
  const sessions = useStore((s) => s.sessionsByItem[id] ?? []);
  const events = useStore((s) => s.eventsByItem[id] ?? []);
  const hydrateItem = useStore((s) => s.hydrateItem);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [tab, setTab] = useState<InspectorTab>("tasks");
  const [selectionByTab, setSelectionByTab] = useState<Record<InspectorTab, Selection>>(EMPTY_SELECTION);
  const [maximizedLog, setMaximizedLog] = useState(false);
  const phone = usePhone();

  const [nodeId, selectNode, nodeExplicit] = useNodeSelection(item?.current_node_id ?? null);

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

  // Defaulting the Tasks selection to the selected node's newest session, so
  // the log pane is never blank the moment a tab opens.
  useEffect(() => {
    if (selectionByTab.tasks.id) return;
    const latest = [...sessions]
      .filter((s) => s.node_id === nodeId)
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
    if (latest) setSelectionByTab((prev) => ({ ...prev, tasks: { kind: "session", id: latest.id } }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, sessions.length]);

  const setSelection = (s: Selection) => setSelectionByTab((prev) => ({ ...prev, [tab]: s }));
  const goToLog = (sessionId: string) => {
    setTab("tasks");
    setSelectionByTab((prev) => ({ ...prev, tasks: { kind: "session", id: sessionId } }));
  };
  // On the phone list (m04) "Review changes" has no split to switch a tab
  // in — it has to open the node page (m05) on the Changes tab instead.
  const goToChanges = () => {
    setTab("changes");
    if (phone && item?.current_node_id) {
      selectNode(item.current_node_id);
      setSelectionByTab(EMPTY_SELECTION);
    }
  };
  // The gate card's "Read <doc>" — same tab-switch shape as goToChanges,
  // for the document the gate is a decision about. Clears the Documents
  // selection every time, not just on phone: a stale selection from an
  // earlier gate's artifact would otherwise survive the tab switch and
  // Documents.tsx's own "select the first once nothing is selected" only
  // fires when `selected` is null (Kraft-esc's own idiom for a fresh stop).
  const goToDocuments = () => {
    setTab("documents");
    setSelectionByTab((prev) => ({ ...prev, documents: EMPTY_SELECTION.documents }));
    if (phone && item?.current_node_id) {
      selectNode(item.current_node_id);
    }
  };
  // The not_started bar's "Edit chain" (06): opens the Config tab.
  const goToConfig = () => {
    setTab("config");
    if (phone && item?.current_node_id) {
      selectNode(item.current_node_id);
      setSelectionByTab(EMPTY_SELECTION);
    }
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
  // current one (`nodeExplicit`, `useNodeSelection` above).
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
          selection={selectionByTab[tab]}
          onSelect={setSelection}
        />
      </div>
    );
  }

  const currentLogSessionId = selectionByTab.tasks.kind === "session" ? selectionByTab.tasks.id : null;

  return (
    <div className="detail item-page">
      {phone && <PhoneTopBar item={item} />}
      <Header item={item} events={events} />

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
        onReadDoc={goToDocuments}
        onEditChain={goToConfig}
      />

      {phone ? (
        <>
          <PhoneStageList
            item={item}
            events={events}
            onSelect={(id) => {
              selectNode(id);
              setSelectionByTab(EMPTY_SELECTION);
            }}
          />
          {currentLogSessionId && (
            <div className="phone-node-log">
              <p className="section-label">Log · {item.current_node_id}</p>
              <Log sessionId={currentLogSessionId} capLines={8} />
            </div>
          )}
        </>
      ) : (
        <GraphSplit
          graph={
            <StageGraph
              item={item}
              selected={nodeId}
              onSelect={(id) => {
                selectNode(id);
                setSelectionByTab(EMPTY_SELECTION);
              }}
            />
          }
          lower={
            <div className="item-split">
              <Inspector
                item={item}
                sessions={sessions}
                events={events}
                nodeId={nodeId}
                tab={tab}
                onTabChange={setTab}
                selection={selectionByTab[tab]}
                onSelect={setSelection}
              />
              <div className="item-right-pane" data-maximized={maximizedLog}>
                <RightPane
                  item={item}
                  events={events}
                  sessions={sessions}
                  nodeId={nodeId}
                  tab={tab}
                  selection={selectionByTab[tab]}
                  onViewLog={goToLog}
                  maximized={maximizedLog}
                  onToggleMaximize={() => setMaximizedLog((v) => !v)}
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
