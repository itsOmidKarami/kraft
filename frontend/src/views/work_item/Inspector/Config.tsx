import { useState } from "react";
import * as api from "../../../api";
import { useStore } from "../../../store";
import { Switch } from "../../../components/ui";
import type { WorkItem } from "../../../types";

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
    </div>
  );
}
