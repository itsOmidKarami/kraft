import { useState } from "react";
import * as api from "../../../api";
import { repoName } from "../../../format";
import { useStore } from "../../../store";
import { Row, RowState, StatusGlyph, Switch } from "../../../components/ui";
import type { SessionStatus, WorkItem } from "../../../types";

/** A repo row's state → the glyph/label vocabulary session rows already use. */
function repoGlyphStatus(state: string): SessionStatus {
  if (state === "merged") return "done";
  if (state === "failed") return "failed";
  if (state === "open") return "running";
  return "pending";
}

/**
 * Inspector · Config (UI v3 · 41, G3-01/02): the selected node's own fields
 * and its auto-escalate toggle — locked once the node has started. The work
 * item block and the effective-chain YAML moved to `RightPane/ConfigPane.tsx`
 * (Config's own right column used to render them inline, before the right
 * pane existed for this tab).
 */
export function Config({
  item,
  nodeId,
}: {
  item: WorkItem;
  nodeId: string | null;
}) {
  const hydrateItem = useStore((s) => s.hydrateItem);
  const chain = item.effective_chain ?? item.chain_definition;
  const node = chain.nodes.find((n) => n.id === nodeId) ?? null;
  const idx = chain.nodes.findIndex((n) => n.id === nodeId);
  const curIdx = chain.nodes.findIndex((n) => n.id === item.current_node_id);
  // Mirrors the server's `node_started`: current or already passed.
  const started = idx >= 0 && curIdx >= 0 && idx <= curIdx;

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const toggleAutoEscalate = async (next: boolean) => {
    if (!node) return;
    setBusy(true);
    setErr(null);
    try {
      await api.updateWorkItem(item.id, { node_overrides: { [node.id]: { auto_escalate: next } } });
      await hydrateItem(item.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="inspector-config" data-testid="inspector-config">
      <section className={`config-block${started ? " config-locked" : ""}`}>
        <p className="section-label">
          Node · {node?.id ?? "—"}
          {started && " · 🔒 locked · started"}
        </p>
        {node ? (
          <>
            <dl className="config-fields">
              <dt>tasks</dt>
              <dd>{node.tasks.join(", ")}</dd>
              <dt>gate_after</dt>
              <dd>{node.gate_after ?? "—"}</dd>
              <dt>fix_loop</dt>
              <dd>{node.fix_loop ?? "—"}</dd>
              <dt>auto-escalate</dt>
              <dd>{node.auto_escalate ? "on" : "off"}</dd>
              <dt>on_failure</dt>
              <dd>{node.on_failure?.length ? node.on_failure.join(", ") : "—"}</dd>
            </dl>
            <label className="control-row config-switch">
              <Switch
                checked={!!node.auto_escalate}
                onChange={toggleAutoEscalate}
                label="auto-escalate this node's gate"
                disabled={busy || started || !node.gate_after}
              />
              auto-escalate
            </label>
            {started && (
              <p className="config-readonly">
                {node.id} has started — a node's config is locked once it starts. Select a later node
                (plan onward) to change it for this work item.
              </p>
            )}
          </>
        ) : (
          <p className="empty">select a node in the graph above</p>
        )}
      </section>
      {err && <p className="form-error">{err}</p>}
      {/* W0.7: the per-repo list used to sit inline under the hero and push
          the split off the page on a 12-repo item. It is configuration, so it
          lives here; the hero's "+N submodules" chip scrolls to it. */}
      {!!item.repos?.length && (
        <section className="config-block config-repos" id="config-repos" data-testid="config-repos">
          <p className="section-label">Repos · merge rank, deepest first</p>
          <p className="config-repos-policy">
            root_merge_policy: <b>{item.root_merge_policy}</b>
          </p>
          {item.repos.map((r) => (
            <Row key={r.path} columns="22px minmax(0, 1fr) auto auto" data-repo={r.path}>
              <StatusGlyph status={repoGlyphStatus(r.state)} />
              <span className="row-text">
                <span className="row-title" title={r.repo}>
                  {repoName(r.repo)}
                </span>
                {/* Shown whole, broken anywhere (W5.2): the tail tells two
                    submodules apart, the head which checkout they are in. */}
                <span className="row-sub config-repo-path path" title={r.path}>
                  {r.path}
                </span>
              </span>
              <span className="row-sub">
                {r.role} · rank {r.merge_rank}
              </span>
              <RowState status={repoGlyphStatus(r.state)}>{r.state}</RowState>
            </Row>
          ))}
        </section>
      )}
    </div>
  );
}
