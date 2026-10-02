import { useState } from "react";
import { repoName, tokens, usd } from "../../../format";
import type { KraftEvent, Policy, WorkItemDocument } from "../../../types";
import { Button } from "../../ui/Button";
import { showToast } from "../../ui/Toast";
import { act } from "../actions";
import type { Applied } from "../draft/applied";
import { appliedRows } from "../draft/AppliedRows";
import { age, eventLine } from "../events";
import type { ItemDetail } from "../useItem";
import { chainName } from "../chainName";
import { budgetRaise } from "../status";
import { limitPolicy } from "../limitPolicy";

const statusLine = (item: ItemDetail) => {
  const st = item.display_status ?? "running";
  if (st === "needs_you" && item.stop?.kind === "gate") return `waiting for you at ${item.pending_gate ?? item.stop.node}`;
  const step = item.summary?.step;
  const live = !["done", "archived", "cancelled"].includes(st);
  return [st.replace("_", " "), live && step ? `step ${step.index} of ${step.count}` : "", live ? item.current_node_id ?? "" : ""].filter(Boolean).join(" · ");
};

/** The chain pane's Overview (Decisions §5 Chain pane): status, progress,
 *  current (a link), spend, the documents attached at intake (each opens when
 *  the caller can open it) and Recent, whose lines select their node. */
export function ChainOverview({ item, events, now, onSelect, docs, onDoc }: { item: ItemDetail; events: KraftEvent[]; now: number; onSelect: (node: string) => void; docs?: WorkItemDocument[]; onDoc?: (d: WorkItemDocument) => void }) {
  const sum = item.summary;
  const cap = item.budget_cap;
  const recent = [...events].reverse().flatMap((e) => {
    const line = eventLine(e);
    return line ? [{ e, line }] : [];
  }).slice(0, 20);
  const live = !["done", "archived", "cancelled"].includes(item.display_status ?? "");
  return (
    <>
      <dl className="item-facts ip-facts">
        <div><dt>status</dt><dd>{statusLine(item)}</dd></div>
        {sum && <div><dt>progress</dt><dd>{sum.nodes_done} of {sum.nodes_total} nodes · {sum.gates_passed} {sum.gates_passed === 1 ? "gate" : "gates"} passed</dd></div>}
        {live && item.current_node_id && <div><dt>current</dt><dd><button type="button" className="item-link is-strong is-mono" onClick={() => onSelect(item.current_node_id!)}>{item.current_node_id}</button></dd></div>}
        {!!item.attachments?.length && (
          <div>
            <dt title="attached at intake">attached</dt>
            <dd>
              {item.attachments.map((a) => {
                const doc = docs?.find((d) => d.attachment_kind === a.kind);
                const name = a.path.split("/").at(-1);
                return (
                  <span key={a.kind} className="ip-attached">
                    {a.kind}{" "}
                    {doc && onDoc ? <button type="button" className="item-link is-mono" title={a.path} onClick={() => onDoc(doc)}>{name}</button> : <span className="is-mono" title={a.path}>{name}</span>}
                  </span>
                );
              })}
            </dd>
          </div>
        )}
        {cap && <div><dt>spent</dt><dd>{usd(cap.spent_usd)}{cap.cap_usd != null ? ` of ${usd(cap.cap_usd)}` : ""}</dd></div>}
      </dl>
      <h3 className="ip-h">Recent</h3>
      {recent.length ? (
        <ol className="ip-recent">
          {recent.map(({ e, line }) => (
            <li key={e.seq}>
              <span className="ip-recent-age">{age(e.created_at, now)}</span>
              {e.node_id ? <button type="button" className="ip-recent-line" onClick={() => onSelect(e.node_id!)}>{line}</button> : <span className="ip-recent-line">{line}</span>}
            </li>
          ))}
        </ol>
      ) : <p className="item-muted">Nothing yet.</p>}
    </>
  );
}

function Meter({ label, used, of, ratio, max, onEdit }: { label: string; used: string; of: string | null; ratio?: number; max?: string; onEdit?: () => void }) {
  const tone = ratio == null ? "" : ratio >= 1 ? " is-bad" : ratio > 0.75 ? " is-warn" : "";
  return (
    <div className={`meter${tone}`}>
      <div className="meter-row">
        <span className="meter-label">{label}</span>
        <span className="meter-value"><strong>{used}</strong>{of ? <> of {of}</> : " · no cap"}</span>
        {onEdit && <button type="button" className="icon-btn meter-edit" aria-label={`Edit ${label.toLowerCase()}`} onClick={onEdit}>✎</button>}
      </div>
      {ratio != null && (
        <div className="meter-bar-row">
          <span className="meter-bar" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(Math.min(ratio, 1) * 100)}>
            <span className="meter-fill" style={{ width: `${Math.min(ratio, 1) * 100}%` }} />
          </span>
          {max && <span className="meter-max">{max}</span>}
        </div>
      )}
    </div>
  );
}

/** Raise the budget: quick picks and a typed value (GAP §2 #16). A budget stop
 *  on the item's own cap goes through /budget/raise, which also retries; one
 *  on a policy cap (`stop.limit`) patches that policy and retries; otherwise,
 *  a daily or token stop among them, PATCH budget_usd, which /budget/raise
 *  would refuse there. Off a stop it changes the cap in force, the item's
 *  policy one when `budget_cap.key` says so. */
export const STILL_STOPPED = "Saved the item's cap. It is still stopped: another cap stopped it, so Retry once that one is raised.";

function BudgetEditor({ item, onDone, onCancel }: { item: ItemDetail; onDone: () => void; onCancel: () => void }) {
  // A stop's limit, else the item's policy cap when that is the one in force: the pencil changes the cap it shows.
  const limit = item.stop?.kind === "budget" ? item.stop.limit
    : item.budget_cap?.key === "policy.budget_usd" ? { path: "", key: "budget_usd" as const, value: item.budget_cap.cap_usd ?? 0, maximum: null }
    : undefined;
  const cap = limit?.value ?? item.budget_cap?.cap_usd ?? 0;
  const [value, setValue] = useState(String(cap || ""));
  const [error, setError] = useState<string | null>(null);
  const send = async (usdCap: number | null) => {
    let r;
    if (limit && usdCap != null) {
      r = await act.patch(item.id, limitPolicy(item.policy_override, limit, usdCap));
      if (r.ok && item.stop?.kind === "budget") r = await act.retry(item.id);
    } else r = budgetRaise(item) === "item" ? await act.raiseBudget(item.id, usdCap) : await act.patch(item.id, { budget_usd: usdCap });
    if (!r.ok) return setError(r.error);
    // A budget stop this cap did not make: saving it retries nothing.
    if (item.stop?.kind === "budget" && !budgetRaise(item)) showToast(STILL_STOPPED);
    onDone();
  };
  return (
    <div className="meter-editor">
      <div className="item-actions">
        <Button onClick={() => send(cap + 5)}>+$5</Button>
        <Button onClick={() => send(cap + 10)}>+$10</Button>
        {!limit && <Button onClick={() => send(null)}>No cap</Button>}
      </div>
      <div className="item-actions">
        <label className="item-check">$ <input aria-label="Budget in dollars" className="item-input meter-input" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} /></label>
        <Button variant="primary" disabled={!(Number(value) > 0)} onClick={() => send(Number(value))}>Save</Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
      {error && <p className="item-error" role="alert">{error}</p>}
    </div>
  );
}

/** The item's own policy override as Config rows: an item-wide field by its
 *  name (`budget_usd $1.00`), one on a path by the path (`verification
 *  max_attempts 5`). Raise cap writes these. */
export function policyRows(o: ItemDetail["policy_override"]): [string, string][] {
  const val = (k: string, v: unknown) => (k === "budget_usd" ? (typeof v === "number" ? usd(v) : "no cap") : String(v));
  const { paths, ...wide } = o ?? {};
  return [
    ...Object.entries(wide).filter(([, v]) => v != null).map(([k, v]) => [k, val(k, v)] as [string, string]),
    ...Object.entries(paths ?? {}).map(([p, f]) => [p, Object.entries(f).filter(([, v]) => v != null).map(([k, v]) => `${k} ${val(k, v)}`).join(", ")] as [string, string]),
  ];
}

/** The chain pane's Config: the item settings (Decisions §3, §14 Budget layers).
 *  Only what the API reports is drawn (Kraft-x8qzu: no running-time, wall-clock
 *  or token caps; Kraft-o114l: auto gate is read-only; Kraft-k3vq1: the budget
 *  override has no reset). */
export function ChainConfig({ item, policy, reload, editBudget, onEditBudget, applied }: { item: ItemDetail; policy: Policy | null; reload: () => void; editBudget: boolean; onEditBudget: (on: boolean) => void; applied?: Record<string, Applied> }) {
  const [error, setError] = useState<string | null>(null);
  const cap = item.budget_cap;
  const daily = cap?.daily;
  const used = item.usage?.total;
  const overrides = Object.entries(item.node_overrides ?? {});
  const draftRows = appliedRows(applied);
  const agent = (item as { agent_overrides?: Record<string, unknown> | null }).agent_overrides;
  const reset = async (body: Record<string, unknown>) => {
    setError(null);
    const r = await act.patch(item.id, body);
    if (r.ok) reload();
    else setError(r.error);
  };
  const policyCap = policy?.budget?.work_item_usd;
  const ownPolicy = policyRows(item.policy_override);
  // The item's own cap, whenever it set one, even while its policy's is the lower one.
  const ownCap = !!item.budget_set;
  return (
    <>
      <h3 className="ip-h">Limits in use</h3>
      {cap && (
        <Meter
          label="Budget"
          used={usd(cap.spent_usd)}
          of={cap.cap_usd != null ? usd(cap.cap_usd) : null}
          ratio={cap.cap_usd ? cap.spent_usd / cap.cap_usd : undefined}
          onEdit={() => onEditBudget(!editBudget)}
        />
      )}
      {editBudget && <BudgetEditor item={item} onCancel={() => onEditBudget(false)} onDone={() => { onEditBudget(false); reload(); }} />}
      {used && <Meter label="Tokens" used={tokens(used.tokens_in + used.tokens_out)} of={null} />}
      {daily && <Meter label="Today, all items" used={usd(daily.spent_usd)} of={daily.cap_usd != null ? usd(daily.cap_usd) : null} ratio={daily.cap_usd ? daily.spent_usd / daily.cap_usd : undefined} max="policy · shared" />}
      <p className="item-muted">Whichever limit is reached first stops the item. An item cap can't go above the policy maximum.</p>
      <dl className="item-facts ip-facts ip-gap">
        <div><dt>auto gate</dt><dd>{item.auto_gate ? "on · an agent may review a gate before you" : "off · every gate waits for you"}</dd></div>
      </dl>

      <h3 className="ip-h">Changed for this item</h3>
      {!ownCap && !ownPolicy.length && !overrides.length && !draftRows.length && !(agent && Object.keys(agent).length) ? (
        <p className="item-muted">Nothing changed. This item runs the chain and policy as frozen.</p>
      ) : (
        <ul className="ip-overrides">
          {ownCap && <li><span className="is-mono">budget</span> {item.budget_usd != null ? usd(item.budget_usd) : "no cap"}{policyCap != null && <span className="item-muted"> · policy {usd(policyCap)}</span>}</li>}
          {ownPolicy.map(([k, v]) => <li key={k}><span className="is-mono">{k}</span> {v} <span className="item-muted">· item policy</span></li>)}
          {agent && Object.keys(agent).length > 0 && (
            <li><span className="is-mono">model</span> {Object.entries(agent).map(([k, v]) => `${k} ${v}`).join(", ")} <button type="button" className="item-link" onClick={() => reset({ agent_overrides: {} })}>reset</button></li>
          )}
          {overrides.map(([node, o]) => (
            <li key={node}><span className="is-mono">{node}</span> {Object.entries(o).map(([k, v]) => `${k} ${v}`).join(", ")} <button type="button" className="item-link" onClick={() => reset({ node_overrides: { [node]: {} } })}>reset</button></li>
          ))}
          {draftRows.map((r) => <li key={r.path}><span className="is-mono">{r.path}</span> {r.text} <span className="item-muted">applied by the draft</span></li>)}
        </ul>
      )}
      {error && <p className="item-error" role="alert">{error}</p>}

      {policy && (
        <details className="ip-rest">
          <summary>Everything else · policy values</summary>
          <dl className="item-facts ip-facts">
            {policy.default && <div><dt>fix loops</dt><dd>{policy.default.attempts} attempts{policy.default.wall_clock_s ? ` · ${Math.round(policy.default.wall_clock_s / 60)}m` : ""}</dd></div>}
            {policy.rate_limit_retries != null && <div><dt>rate-limit retries</dt><dd>{policy.rate_limit_retries}</dd></div>}
            {policy.auto_escalate_stuck != null && <div><dt>auto-escalate</dt><dd>{policy.auto_escalate_stuck ? "on" : "off"}{policy.auto_escalate_delay_s ? ` · after ${Math.round(policy.auto_escalate_delay_s / 60)}m` : ""}</dd></div>}
            {policy.budget?.daily_usd != null && <div><dt>daily, all items</dt><dd>{usd(policy.budget.daily_usd)}</dd></div>}
          </dl>
        </details>
      )}

      {(item.repos?.length ?? 0) > 1 && (
        <>
          <h3 className="ip-h">Repos in this item</h3>
          <ol className="ip-repos">
            {[...item.repos!].sort((a, b) => a.merge_rank - b.merge_rank).map((r) => <li key={r.path}><span className="is-mono">{repoName(r.path)}</span> <span className="item-muted">{r.role} · merge {r.merge_rank + 1}</span></li>)}
          </ol>
        </>
      )}
      <p className="ip-foot is-mono">{chainName(item)} chain · {repoName(item.repo)} · frozen at intake</p>
    </>
  );
}
