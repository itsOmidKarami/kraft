import { useState } from "react";
import * as api from "../../../api";
import { usd } from "../../../format";
import { useStore } from "../../../store";
import { Switch } from "../../../components/ui";
import type { WorkItem } from "../../../types";

/**
 * Inspector · Config (UI v2 · 05, 11): NODE config for the selected node,
 * the WORK ITEM block, and the effective-chain YAML — the UI for UI v2 · 04's
 * overrides API. "Save as template…" has no API yet (the notes allow
 * beading it out); filed as Kraft-9ba7.
 */

function yamlOf(item: WorkItem, nodeId: string | null): string {
  const chain = item.effective_chain ?? item.chain_definition;
  const overrides = item.node_overrides ?? {};
  const lines: string[] = [`template_id: ${chain.template_id}`, "nodes:"];
  for (const n of chain.nodes) {
    const isOverridden = Object.keys(overrides[n.id] ?? {}).length > 0;
    const mark = isOverridden ? "  # override" : "";
    const cur = n.id === nodeId ? " # ← selected" : "";
    lines.push(`  - id: ${n.id}${cur}`);
    lines.push(`    gate_after: ${n.gate_after ?? "null"}`);
    if (n.auto_escalate != null) lines.push(`    auto_escalate: ${n.auto_escalate}${mark}`);
  }
  return lines.join("\n");
}

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
  const [budgetDraft, setBudgetDraft] = useState(() => String(item.budget_cap?.cap_usd ?? ""));
  const [noCap, setNoCap] = useState(item.budget_cap?.cap_usd == null && item.budget_cap?.source === "item");

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

  const resetToTemplate = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.resetChainOverrides(item.id);
      await hydrateItem(item.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const saveBudget = async () => {
    setBusy(true);
    setErr(null);
    try {
      let value: number | null | undefined;
      if (noCap) {
        value = null;
      } else if (budgetDraft.trim() === "") {
        value = undefined;
      } else {
        const n = Number(budgetDraft);
        if (!Number.isFinite(n) || n < 0) {
          setErr(`"${budgetDraft}" is not a valid budget — enter a non-negative number`);
          return;
        }
        value = n;
      }
      await api.updateWorkItem(item.id, { budget_usd: value });
      await hydrateItem(item.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const overridesCount = item.node_overrides_count ?? 0;
  const started_item = item.current_node_id != null;

  return (
    <div className="inspector-config" data-testid="inspector-config">
      <section className="config-block">
        <p className="section-label">NODE · {node?.id.toUpperCase() ?? "—"}</p>
        {node ? (
          <>
            <dl className="config-fields">
              <dt>tasks</dt>
              <dd className="mono">{node.tasks.join(", ")}</dd>
              <dt>gate_after</dt>
              <dd>{node.gate_after ?? "—"}</dd>
              {node.fix_loop && (
                <>
                  <dt>fix_loop</dt>
                  <dd>{node.fix_loop}</dd>
                </>
              )}
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
              <p className="field-hint">a node's config is locked once it starts</p>
            )}
          </>
        ) : (
          <p className="empty">select a node in the graph above</p>
        )}
      </section>

      <section className="config-block">
        <p className="section-label">WORK ITEM</p>
        <dl className="config-fields">
          <dt>template</dt>
          <dd>{item.chain_template}</dd>
          <dt>budget</dt>
          <dd>
            {item.budget_cap?.cap_usd != null
              ? `${usd(item.budget_cap.cap_usd)} · ${usd(item.budget_cap.spent_usd)} used`
              : `no cap · ${usd(item.budget_cap?.spent_usd ?? 0)} used`}
          </dd>
          <dt>policy</dt>
          <dd>{item.budget_cap?.source === "item" ? "item" : "inherited"}</dd>
        </dl>
        <div className="control-row">
          <label htmlFor="config-no-cap" className="control-row">
            <input
              id="config-no-cap"
              type="checkbox"
              checked={noCap}
              onChange={(e) => setNoCap(e.target.checked)}
            />
            no cap
          </label>
          {!noCap && (
            <input
              className="input config-budget-input"
              inputMode="decimal"
              placeholder="$ policy default"
              value={budgetDraft}
              onChange={(e) => setBudgetDraft(e.target.value)}
              disabled={busy}
            />
          )}
          <button className="btn btn-secondary" disabled={busy} onClick={saveBudget}>
            Save budget
          </button>
        </div>
      </section>

      <section className="config-block">
        <div className="control-row">
          <p className="section-label">
            Effective chain · this work item · default
            {overridesCount > 0 ? ` + ${overridesCount} override${overridesCount === 1 ? "" : "s"}` : ""}
          </p>
          <button className="btn btn-ghost" disabled={busy || started_item} onClick={resetToTemplate}>
            Reset to template
          </button>
          <button className="btn btn-ghost" disabled title="no template-write API yet — Kraft-9ba7">
            Save as template…
          </button>
        </div>
        <pre className="config-yaml mono">{yamlOf(item, nodeId)}</pre>
        <p className="inspector-foot"># override marks a field this item changed from the template.</p>
      </section>
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}
