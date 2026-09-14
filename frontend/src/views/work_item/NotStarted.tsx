import { Play } from "@phosphor-icons/react";
import * as api from "../../api";
import { SectionLabel } from "../../components/ui";
import { ago, repoName } from "../../format";
import type { WorkItem } from "../../types";
import { useActionBar } from "./ActionBar/useActionBar";

/**
 * A never-started item (spec 21, Kraft-pfqdb): the split's left pane is the
 * intake card -- what it will run with and the one thing to do, Start -- in
 * place of an inspector whose every tab is still empty; the right pane
 * describes the chain it will walk.
 */
export function NotStartedCard({ item }: { item: WorkItem }) {
  const { busy, err, run } = useActionBar(item.id);
  const nodes = item.chain_definition.nodes;
  const firstGate = nodes.find((n) => n.gate_after)?.gate_after ?? "none";
  const cap = item.budget_cap;
  return (
    <div className="card not-started-card" data-testid="not-started-card">
      <p className="attention-title">Waiting for you to start it</p>
      <p className="field-hint">
        Filed {ago(item.created_at)}. Nothing spends tokens until a person clicks Start.
      </p>
      <dl className="config-fields">
        <dt>template</dt>
        <dd>{item.chain_template}</dd>
        <dt>repo</dt>
        <dd title={item.repo}>{repoName(item.repo)}</dd>
        <dt>budget</dt>
        <dd>
          {cap?.cap_usd != null
            ? `$${cap.cap_usd} · ${cap.source === "policy" ? "policy default" : "set on this item"}`
            : "no cap"}
        </dd>
        <dt>will start at</dt>
        <dd>{nodes[0]?.id ?? "—"}</dd>
        <dt>first gate</dt>
        <dd>{firstGate}</dd>
      </dl>
      <div className="gate-actions">
        <button
          className="btn btn-primary"
          disabled={busy}
          onClick={() => run(() => api.resumeWorkItem(item.id), "Started")}
        >
          <Play size={14} /> Start
        </button>
      </div>
      {err && <p className="form-error">{err}</p>}
    </div>
  );
}

export function ChainDescription({ item }: { item: WorkItem }) {
  const nodes = item.chain_definition.nodes;
  const gates = nodes.filter((n) => n.gate_after).length;
  return (
    <div className="not-started-chain" data-testid="not-started-chain">
      <SectionLabel>
        Chain · {item.chain_template} · {nodes.length} node{nodes.length === 1 ? "" : "s"}, {gates} gate
        {gates === 1 ? "" : "s"}
      </SectionLabel>
      <ol>
        {nodes.map((n) => (
          <li key={n.id}>
            <code>{n.id}</code>
            <span className="row-sub">
              {" "}
              {n.tasks.length} task{n.tasks.length === 1 ? "" : "s"}
              {n.fix_loop && ` · fix loop ${n.fix_loop}`}
              {n.gate_after && ` · then gate ${n.gate_after}`}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
