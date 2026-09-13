import { useEffect, useMemo, useState } from "react";
import * as api from "../api";
import { elapsed, repoName, tokens, usd } from "../format";
import { useStore } from "../store";
import type { Analytics as Report } from "../types";
import "./analytics.css";

/**
 * Analytics (design 35, m15): where the time and the money went. Everything
 * here is a rollup of captured `worker_session` usage and event history;
 * nothing is estimated.
 */

const WEEKS = 8;

function weekBuckets(report: Report): { label: string; n: number; partial: boolean }[] {
  // The server keys each bucket by the Monday of the event's *UTC* date, so the
  // columns have to be generated in UTC too — building them from local date parts
  // silently reads 0 for the newest weeks anywhere far enough ahead of UTC.
  const now = new Date();
  const monday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7));
  const byWeek = new Map(report.weekly_merged.map((w) => [w.week_start, w.n]));
  return Array.from({ length: WEEKS }, (_, i) => {
    const d = new Date(monday);
    d.setUTCDate(d.getUTCDate() - (WEEKS - 1 - i) * 7);
    return {
      label: `${d.getUTCMonth() + 1}/${d.getUTCDate()}`,
      n: byWeek.get(d.toISOString().slice(0, 10)) ?? 0,
      partial: i === WEEKS - 1,
    };
  });
}

function Kpi({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="kpi">
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      <span className="kpi-sub">{sub}</span>
    </div>
  );
}

const uniq = (xs: string[]) => [...new Set(xs)].sort().map((x) => ({ id: x, name: x }));
/** Repo options key on the full path but read as the repo's own name. */
const uniqRepos = (xs: string[]) =>
  [...new Set(xs)].sort().map((x) => ({ id: x, name: repoName(x) }));

/** "plan_approval" → "plan", "human_review_approval" → "human_review" — the
 *  short form the KPI sub-line uses (design 35: "5 plan · 2 human_review"). */
const shortGate = (gate: string) => gate.replace(/_approval$/, "");

export function AnalyticsView() {
  const items = useStore((s) => Object.values(s.workItems));
  const [repo, setRepo] = useState<string | null>(null);
  const [tpl, setTpl] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // the report itself has its own error path; this only fills the selects
    useStore.getState().bootstrap().catch(() => {});
  }, []);

  useEffect(() => {
    let live = true;
    setError(null);
    api
      .getAnalytics({ range: "8w", repo: repo ?? undefined, template: tpl ?? undefined })
      .then((r) => live && setReport(r))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [repo, tpl]);

  const weeks = useMemo(() => (report ? weekBuckets(report) : []), [report]);
  const peak = Math.max(1, ...weeks.map((w) => w.n));
  const topCost = Math.max(...(report?.by_node.map((n) => n.cost_usd) ?? [0]), 0.000001);
  const totalCost = report?.by_node.reduce((s, n) => s + n.cost_usd, 0) ?? 0;

  const t = report?.totals;
  const repoOptions = uniqRepos(items.map((i) => i.repo));
  const tplOptions = uniq(items.map((i) => i.chain_template));

  const rejectedSub = report?.rejected_gates_by_gate.length
    ? report.rejected_gates_by_gate
        .slice(0, 2)
        .map((g) => `${g.n} ${shortGate(g.gate)}`)
        .join(" · ")
    : "none";

  const completedDelta = t?.completed_prev != null ? t.completed - t.completed_prev : null;

  return (
    <div className="analytics analytics-body">
      <div className="analytics-head">
        <div>
          <h2>Analytics</h2>
          <span className="analytics-scope">
            completed work items · what they cost and where the time went
          </span>
        </div>
        <div className="analytics-filters">
          <span className="chip chip-static">last 8 weeks</span>
          <label className="chip" data-active={repo != null || undefined}>
            repo:
            <select value={repo ?? ""} onChange={(e) => setRepo(e.target.value || null)}>
              <option value="">all</option>
              {repoOptions.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
          <label className="chip" data-active={tpl != null || undefined}>
            template:
            <select value={tpl ?? ""} onChange={(e) => setTpl(e.target.value || null)}>
              <option value="">all</option>
              {tplOptions.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {error && <p className="form-error">{error}</p>}
      {!report && !error && <p className="empty">loading…</p>}

      {t && (
        <>
          <div className="kpis">
            <Kpi
              label="Completed"
              value={String(t.completed)}
              sub={
                completedDelta == null
                  ? "no previous period"
                  : `${completedDelta >= 0 ? "+" : ""}${completedDelta} vs previous 8 weeks`
              }
            />
            <Kpi label="Median lead time" value={elapsed(t.median_lead_ms)} sub="create → merge" />
            <Kpi
              label="Human wait"
              value={`${t.human_wait_pct}%`}
              sub="of lead time spent at gates"
            />
            <Kpi
              label="Fix cycles"
              value={t.fix_cycles.toFixed(1)}
              sub={`per verify · ${t.fix_cycles_capped} capped out`}
            />
            <Kpi
              label="Cost"
              value={usd(t.cost_usd, t.cost_complete)}
              sub={
                t.completed
                  ? `${usd(t.cost_usd / t.completed)} per completed item`
                  : "nothing completed yet"
              }
            />
            <Kpi label="Rejected gates" value={String(t.rejected_gates)} sub={rejectedSub} />
          </div>

          <section className="chart">
            <div className="chart-head">
              <span className="chart-title">Throughput by week</span>
              <span className="chart-note">completed items · current week partial</span>
            </div>
            {weeks.every((w) => w.n === 0) ? (
              <p className="empty chart-empty">nothing completed in this range</p>
            ) : (
              <>
                <div className="bars" style={{ gridTemplateColumns: `repeat(${WEEKS}, 1fr)` }}>
                  {weeks.map((w) => (
                    <div key={w.label} className="bar-col" data-partial={w.partial || undefined}>
                      <span className="bar-n">{w.n}</span>
                      <span
                        className="bar"
                        style={{ height: `${Math.round((w.n / peak) * 100)}%` }}
                      />
                    </div>
                  ))}
                </div>
                <div
                  className="bar-labels"
                  style={{ gridTemplateColumns: `repeat(${WEEKS}, 1fr)` }}
                >
                  {weeks.map((w) => (
                    <span key={w.label}>{w.label}</span>
                  ))}
                </div>
              </>
            )}
          </section>

          <div className="analytics-tables">
            <section className="table-block by-node">
              <div className="table-title">Where the time and money go · by node</div>
              <div className="node-row node-head">
                <span>node</span>
                <span>share of cost</span>
                <span>min</span>
                <span>tokens</span>
                <span>$</span>
                <span>%</span>
              </div>
              {report!.by_node.map((n) => (
                <div key={n.node} className="node-row" data-node={n.node}>
                  <span className="node-name" data-label="node">
                    {n.node}
                  </span>
                  <span className="share" data-label="share of cost">
                    <span style={{ width: `${(n.cost_usd / topCost) * 100}%` }} />
                  </span>
                  <span className="num" data-label="min">
                    {Math.round(n.avg_ms / 60_000)}
                  </span>
                  <span className="num" data-label="tokens">
                    {tokens(n.tokens)}
                  </span>
                  <span className="num strong" data-label="$">
                    {usd(n.cost_usd, n.cost_complete)}
                  </span>
                  <span className="num" data-label="%">
                    {totalCost ? Math.round((n.cost_usd / totalCost) * 100) : 0}%
                  </span>
                </div>
              ))}
              <p className="table-foot">
                Minutes are median per completed item. Tokens and dollars are what agents
                reported; a trailing "+" marks a sum missing a session.
              </p>
            </section>

            <div className="table-col">
              <section className="table-block by-repo">
                <div className="table-title">By repo</div>
                <div className="repo-row repo-head">
                  <span>repo</span>
                  <span>items</span>
                  <span>done</span>
                  <span>cost</span>
                  <span>cycles</span>
                </div>
                {report!.by_repo.map((r) => (
                  <div key={r.repo} className="repo-row" data-repo={r.repo}>
                    <span title={r.repo} data-label="repo">
                      {repoName(r.repo)}
                    </span>
                    <span className="num" data-label="items">
                      {r.items}
                    </span>
                    <span className="num" data-label="done">
                      {r.done}
                    </span>
                    <span className="num strong" data-label="cost">
                      {usd(r.cost_usd, r.cost_complete)}
                    </span>
                    <span className="num" data-label="cycles">
                      {r.cycles}
                    </span>
                  </div>
                ))}
              </section>

              <section className="table-block stop-reasons">
                <div className="table-title">Why items stopped for a person</div>
                {report!.stop_reasons.length === 0 && (
                  <p className="empty">nothing stopped for a person in this range</p>
                )}
                {report!.stop_reasons.map((s) => {
                  const max = report!.stop_reasons[0]?.n || 1;
                  return (
                    <div key={s.label} className="stop-row" data-label={s.label}>
                      <span className="stop-label">{s.label}</span>
                      <span className="stop-bar">
                        <span style={{ width: `${(s.n / max) * 100}%` }} />
                      </span>
                      <span className="stop-n">{s.n}</span>
                    </div>
                  );
                })}
              </section>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
