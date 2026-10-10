import { useEffect, useRef, useState, type Ref } from "react";
import { dollars, DOLLARS_HINT, dollarsText, repoName, tokenText, tokenTip, usd } from "../../../format";
import type { KraftEvent, Policy, WorkItemDocument } from "../../../types";
import { Button } from "../../ui/Button";
import { showToast } from "../../ui/Toast";
import { act } from "../actions";
import type { Applied } from "../draft/applied";
import { appliedRows } from "../draft/applied";
import { useDraft } from "../draft/context";
import { lines } from "../draft/view";
import { age, recent as recentLines } from "../events";
import type { ItemDetail } from "../useItem";
import { chainName } from "../chainName";
import { DocViewer } from "../DocViewer";
import { budgetRaise, neverStarted, spentLine } from "../status";
import { notStarted } from "../chainValues";
import { ItemAgentRows } from "./ItemOverrides";
import { limitPolicy } from "../limitPolicy";
import { tip } from "../../ui/Tooltip";

const statusLine = (item: ItemDetail) => {
  const st = item.display_status ?? "running";
  // As the header's badge says it: filed and never started, not paused.
  if (neverStarted(item)) return "not started";
  if (st === "needs_you" && item.stop?.kind === "gate") return `waiting for you at ${item.pending_gate ?? item.stop.node}`;
  const step = item.summary?.step;
  const live = !["done", "archived", "cancelled"].includes(st);
  return [st.replace("_", " "), live && step ? `step ${step.index} of ${step.count}` : "", live ? item.current_node_id ?? "" : ""].filter(Boolean).join(" · ");
};

const RECENT = 5;

/** The chain pane's Overview (Decisions §5 Chain pane): status, progress,
 *  current (a link), spend, the documents attached at intake (each opens when
 *  the caller can open it) and Recent, whose lines select their node. */
export function ChainOverview({ item, events, now, onSelect, docs, onDoc, onMore, where }: { item: ItemDetail; events: KraftEvent[]; now: number; onSelect: (node: string) => void; docs?: WorkItemDocument[]; onDoc?: (d: WorkItemDocument) => void; onMore?: () => void; where?: boolean }) {
  const sum = item.summary;
  const cap = item.budget_cap;
  // The last few lines of the story (WI-2); the rest behind a link: the caller's (the peek's Activity) or in place.
  const [all, setAll] = useState(false);
  const story = recentLines(events);
  const recent = all ? story : story.slice(0, RECENT);
  const live = !["done", "archived", "cancelled"].includes(item.display_status ?? "");
  const [attached, setAttached] = useState<string | null>(null);
  return (
    <>
      {attached && <DocViewer source={{ kind: "attachment", workItemId: item.id, attachment: attached }} onClose={() => setAttached(null)} />}
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
                // The kind is in the name: a spec and its plan are often both <x>.md (R11b-07).
                const label = `${a.kind.charAt(0).toUpperCase()}${a.kind.slice(1)}: ${name}`;
                return (
                  <span key={a.kind} className="ip-attached">
                    {a.kind}{" "}
                    {doc && onDoc ? <button type="button" className="item-link is-mono" title={a.path} aria-label={label} onClick={() => onDoc(doc)}>{name}</button>
                      // Not indexed yet (before start nothing is): it reads from the copy Kraft kept at intake.
                      : <button type="button" className="item-link is-mono" title={a.path} aria-label={label} onClick={() => setAttached(a.kind)}>{name}</button>}
                  </span>
                );
              })}
            </dd>
          </div>
        )}
        {cap && <div><dt>spent</dt><dd>{spentLine(item)}</dd></div>}
        {/* Off the item page (the board's peek), which chain and repo: the page's crumb says them there. */}
        {where && <div><dt>chain</dt><dd>{chainName(item)} · frozen at intake</dd></div>}
        {where && <div><dt>repo</dt><dd>{repoName(item.repo)}</dd></div>}
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
      {story.length > RECENT && !all && <button type="button" className="item-link ip-recent-more" onClick={onMore ?? (() => setAll(true))}>{onMore ? "All activity" : `Show ${story.length - RECENT} earlier`}</button>}
    </>
  );
}

function Meter({ label, used, of, ratio, max, onEdit, editRef }: { label: string; used: React.ReactNode; of: string | null; ratio?: number; max?: string; onEdit?: () => void; editRef?: Ref<HTMLButtonElement> }) {
  const tone = ratio == null ? "" : ratio >= 1 ? " is-bad" : ratio > 0.75 ? " is-warn" : "";
  return (
    <div className={`meter${tone}`}>
      <div className="meter-row">
        <span className="meter-label">{label}</span>
        <span className="meter-value"><strong>{used}</strong>{of ? <> of {of}</> : " · no cap"}</span>
        {onEdit && <button ref={editRef} type="button" className="icon-btn meter-edit" {...tip(`Edit ${label.toLowerCase()}`)} onClick={onEdit}>✎</button>}
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
  const [value, setValue] = useState(cap ? dollarsText(cap) : "");
  // A decimal comma, as a comma-decimal locale or iOS's decimal keypad types it, is a point; "1,000" is a thousand.
  const amount = dollars(value);
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
      {/* A form, focused on open: Enter saves the typed cap and Escape cancels, so the editor
          Raise cap and ✎ open needs no pointer (R10a-05). */}
      <form className="item-actions" onSubmit={(e) => { e.preventDefault(); if (amount > 0) void send(amount); }}>
        {/* The cap in force is selected on focus, so what is typed replaces it: typing 0.03 after the caret saved $100.03 (R11a-03). */}
        <label className="item-check">$ <input autoFocus aria-label="Budget in dollars" className="item-input meter-input" inputMode="decimal" value={value} onFocus={(e) => e.currentTarget.select()} onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onCancel(); } }} /></label>
        <Button type="submit" variant="primary" disabled={!(amount > 0)}>Save</Button>
        <Button onClick={onCancel}>Cancel</Button>
      </form>
      {value.trim() && Number.isNaN(amount) && <p className="item-muted">{DOLLARS_HINT}</p>}
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
  // The editor's Escape, Cancel and save hand the focus back to ✎, as an override row's do: the field that had it is gone (R11b-03).
  const pencil = useRef<HTMLButtonElement>(null);
  const refocus = useRef(false);
  // What had the focus when the editor opened (✎, or a Raise cap outside), read before the field takes it:
  // with no budget meter there is no ✎ to come back to.
  const opener = useRef<HTMLElement | null>(editBudget && typeof document !== "undefined" ? (document.activeElement as HTMLElement | null) : null);
  const wasEditing = useRef(editBudget);
  if (editBudget && !wasEditing.current && typeof document !== "undefined") opener.current = document.activeElement as HTMLElement | null;
  wasEditing.current = editBudget;
  useEffect(() => {
    if (!editBudget && refocus.current) (pencil.current ?? (opener.current?.isConnected ? opener.current : null))?.focus();
    refocus.current = false;
  }, [editBudget]);
  const closeBudget = () => {
    refocus.current = true;
    onEditBudget(false);
  };
  const used = item.usage?.total;
  const overrides = Object.entries(item.node_overrides ?? {});
  const draftRows = appliedRows(applied);
  // Before the item starts, its agents' model and effort have rows of their own,
  // and are listed below with the rest of what this item changes.
  const fresh = notStarted(item);
  const agent = Object.entries(item.agent_overrides ?? {}).filter(([, v]) => v != null);
  // What the item's draft holds and the run has not passed: not applied until Review & apply.
  const d = useDraft();
  const pending = d ? lines(d.ops).filter((l) => !d.ops[l.index].passed) : [];
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
          editRef={pencil}
        />
      )}
      {editBudget && <BudgetEditor item={item} onCancel={closeBudget} onDone={() => { closeBudget(); reload(); }} />}
      {used && <Meter label="Tokens" used={<span data-tip={tokenTip(used)}>{tokenText(used)}</span>} of={null} />}
      {daily && <Meter label="Today, all items" used={usd(daily.spent_usd)} of={daily.cap_usd != null ? usd(daily.cap_usd) : null} ratio={daily.cap_usd ? daily.spent_usd / daily.cap_usd : undefined} max="policy · shared" />}
      <p className="item-muted">Whichever limit is reached first stops the item. An item cap can't go above the policy maximum.</p>
      <dl className="item-facts ip-facts ip-gap">
        <div><dt>auto gate</dt><dd>{item.auto_gate ? "on · an agent may review a gate before you" : "off · every gate waits for you"}</dd></div>
      </dl>

      {fresh && <ItemAgentRows item={item} reload={reload} />}

      <h3 className="ip-h">Changed for this item</h3>
      {!ownCap && !ownPolicy.length && !overrides.length && !draftRows.length && !agent.length && !pending.length ? (
        <p className="item-muted">Nothing changed. This item runs the chain and policy as frozen.</p>
      ) : (
        <ul className="ip-overrides">
          {ownCap && <li><span className="is-mono">budget</span> {item.budget_usd != null ? usd(item.budget_usd) : "no cap"}{policyCap != null && <span className="item-muted"> · policy {usd(policyCap)}</span>}</li>}
          {ownPolicy.map(([k, v]) => <li key={k}><span className="is-mono">{k}</span> {v} <span className="item-muted">· item policy</span></li>)}
          {agent.length > 0 && (
            <li><span className="is-mono">every agent task</span> {agent.map(([k, v]) => `${k} ${v}`).join(", ")} <span className="item-muted">· item-wide</span> <button type="button" className="item-link" onClick={() => reset({ agent_overrides: {} })}>reset</button></li>
          )}
          {overrides.map(([node, o]) => (
            <li key={node}><span className="is-mono">{node}</span> {Object.entries(o).map(([k, v]) => `${k} ${v}`).join(", ")} <span className="item-muted">· node</span> <button type="button" className="item-link" onClick={() => reset({ node_overrides: { [node]: {} } })}>reset</button></li>
          ))}
          {draftRows.map((r) => <li key={r.path}><span className="is-mono">{r.path}</span> {r.text} <span className="item-muted">applied by the draft</span></li>)}
          {pending.map((l) => <li key={`${l.index}-${l.text}`}><span className="is-mono">{l.text.replace(/^[~+»-] /, "").replace(/\s+/g, " ")}</span> <span className="item-draft">· in the draft, not applied yet</span></li>)}
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
