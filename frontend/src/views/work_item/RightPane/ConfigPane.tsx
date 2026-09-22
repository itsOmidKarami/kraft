import { useState } from "react";
import * as api from "../../../api";
import { usd } from "../../../format";
import { useStore } from "../../../store";
import type { WorkItem } from "../../../types";

/**
 * Config's right pane (UI v3 · 41, G3-01/02): the work item's own fields and
 * the effective-chain YAML — cut out of `Inspector/Config.tsx`, which used
 * to render both inline as its own right column before this pane existed.
 */

interface YamlLine {
  text: string;
  tone: "override" | "selected" | null;
}

export function yamlOf(item: WorkItem, nodeId: string | null): YamlLine[] {
  const chain = item.effective_chain ?? item.chain_definition;
  const overrides = item.node_overrides ?? {};
  const lines: YamlLine[] = [
    { text: `template_id: ${chain.template_id}`, tone: null },
    { text: "nodes:", tone: null },
  ];
  for (const n of chain.nodes) {
    const isOverridden = Object.keys(overrides[n.id] ?? {}).length > 0;
    const selected = n.id === nodeId;
    lines.push({
      text: `  - id: ${n.id}${selected ? " # ← selected" : ""}`,
      tone: selected ? "selected" : null,
    });
    // A V1 gate node reports itself under `gate_after`, which under its own
    // entry reads `gate_after: <its own id>` -- a shape no authored template can
    // have. Print the field a V1 chain actually declares instead.
    lines.push({
      text:
        n.kind === "gate"
          ? `    kind: gate${n.reject_to ? `, reject_to: ${n.reject_to}` : ""}`
          : `    gate_after: ${n.gate_after ?? "null"}`,
      tone: selected ? "selected" : null,
    });
    if (n.auto_escalate != null) {
      lines.push({
        text: `    auto_escalate: ${n.auto_escalate}${isOverridden ? " # override" : ""}`,
        tone: isOverridden ? "override" : selected ? "selected" : null,
      });
    }
  }
  return lines;
}

export function ConfigPane({
  item,
  nodeId,
}: {
  item: WorkItem;
  nodeId: string | null;
}) {
  const hydrateItem = useStore((s) => s.hydrateItem);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [editingBudget, setEditingBudget] = useState(false);
  const [budgetDraft, setBudgetDraft] = useState(() =>
    String(item.budget_cap?.cap_usd ?? ""),
  );
  const [noCap, setNoCap] = useState(
    item.budget_cap?.cap_usd == null && item.budget_cap?.source === "item",
  );

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
          setErr(
            `"${budgetDraft}" is not a valid budget — enter a non-negative number`,
          );
          return;
        }
        value = n;
      }
      await api.updateWorkItem(item.id, { budget_usd: value });
      await hydrateItem(item.id);
      setEditingBudget(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const chain = item.effective_chain ?? item.chain_definition;
  const overridesCount = item.node_overrides_count ?? 0;
  const started_item = item.current_node_id != null;

  return (
    <div className="config-pane pane" data-testid="config-pane">
      <section className="config-block">
        <p className="section-label">Work item</p>
        <dl className="config-fields">
          <dt>template</dt>
          <dd>{item.chain_template}</dd>
          <dt>auto-escalate</dt>
          <dd>{chain.nodes.some((n) => n.auto_escalate) ? "on" : "off"}</dd>
          <dt>budget</dt>
          <dd>
            {item.budget_cap?.cap_usd != null
              ? `${usd(item.budget_cap.cap_usd)} · ${usd(item.budget_cap.spent_usd)} used`
              : `no cap · ${usd(item.budget_cap?.spent_usd ?? 0)} used`}
            {!editingBudget && (
              <button
                className="btn btn-ghost"
                onClick={() => setEditingBudget(true)}
              >
                Edit
              </button>
            )}
          </dd>
          <dt>policy</dt>
          <dd>{item.budget_cap?.source === "item" ? "item" : "inherited"}</dd>
        </dl>
        {editingBudget && (
          <div className="control-row budget-row">
            <label htmlFor="config-no-cap">
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
            <button
              className="btn btn-secondary"
              disabled={busy}
              onClick={saveBudget}
            >
              Save budget
            </button>
          </div>
        )}
      </section>

      <section className="config-block">
        <div className="control-row">
          <p className="section-label">
            Effective chain · this work item ·{" "}
            {overridesCount > 0
              ? `${overridesCount} override${overridesCount === 1 ? "" : "s"}`
              : "default · no overrides"}
          </p>
          <button
            className="btn btn-ghost"
            disabled={busy || started_item}
            onClick={resetToTemplate}
          >
            Reset to template
          </button>
          <button
            className="btn btn-secondary"
            disabled
            title="no template-write API yet — Kraft-9ba7"
          >
            Save as template…
          </button>
        </div>
        <pre className="config-yaml mono">
          {yamlOf(item, nodeId).map((line, i) => (
            <div
              key={i}
              className={
                line.tone ? `yaml-line yaml-${line.tone}` : "yaml-line"
              }
            >
              {line.text}
            </div>
          ))}
        </pre>
        <p className="inspector-foot config-legend">
          <span className="yaml-legend-swatch yaml-override" /> override ·{" "}
          <span className="yaml-legend-swatch yaml-selected" /> selected node ·
          Chains → default opens the full editor
        </p>
      </section>
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}
