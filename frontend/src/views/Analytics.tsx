import { useEffect, useMemo, useState } from "react";
import * as api from "../api";
import { elapsed, repoName, tokens, usd } from "../format";
import { useStore } from "../store";
import type { Analytics as Report } from "../types";

/**
 * Analytics (design 6b): where the time and the money went. Everything here is
 * a rollup of captured `worker_session` usage; nothing is estimated.
 */

const RANGES: { id: string; name: string }[] = [
  { id: "7d", name: "Last 7 days" },
  { id: "30d", name: "Last 30 days" },
  { id: "90d", name: "Last 90 days" },
  { id: "all", name: "All time" },
];

const WEEKS = 7;

/**
 * The last seven week-starts, so the chart keeps its shape when a quiet week
 * merged nothing and the API returned no bucket for it.
 */
function weekBuckets(report: Report): { label: string; n: number; partial: boolean }[] {
  // The server keys each bucket by the Monday of the event's *UTC* date, so the
  // columns have to be generated in UTC too — building them from local date parts
  // silently reads 0 for the newest weeks anywhere far enough ahead of UTC.
  const now = new Date();
  const monday = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()),
  );
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

function Facet({
  label,
  rows,
  value,
  onPick,
}: {
  label: string;
  rows: { id: string; name: string }[];
  value: string | null;
  onPick: (v: string | null) => void;
}) {
  return (
    <div className="facet">
      <div className="section-label">{label}</div>
      {rows.map((r) => (
        <button
          key={r.id}
          className="facet-opt"
          aria-pressed={r.id === value}
          onClick={() => onPick(r.id === value ? null : r.id)}
        >
          {r.name}
        </button>
      ))}
    </div>
  );
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
/** Repo facets key on the full path but read as the repo's own name. */
const uniqRepos = (xs: string[]) =>
  [...new Set(xs)].sort().map((x) => ({ id: x, name: repoName(x) }));

export function AnalyticsView() {
  const items = useStore((s) => Object.values(s.workItems));
  const [range, setRange] = useState("30d");
  const [repo, setRepo] = useState<string | null>(null);
  const [tpl, setTpl] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // the report itself has its own error path; this only fills the facets
    useStore.getState().bootstrap().catch(() => {});
  }, []);

  useEffect(() => {
    let live = true;
    setError(null);
    api
      .getAnalytics({ range, repo: repo ?? undefined, template: tpl ?? undefined })
      .then((r) => live && setReport(r))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [range, repo, tpl]);

  const weeks = useMemo(() => (report ? weekBuckets(report) : []), [report]);
  const peak = Math.max(1, ...weeks.map((w) => w.n));
  const topCost = Math.max(...(report?.by_node.map((n) => n.cost_usd) ?? [0]), 0.000001);

  const t = report?.totals;
  const scope = [
    RANGES.find((r) => r.id === range)!.name.toLowerCase(),
    repo ?? "all repos",
    tpl ?? "all templates",
  ].join(" · ");

  return (
    <div className="board analytics">
      <aside className="board-sidebar">
        <Facet label="Range" rows={RANGES} value={range} onPick={(v) => setRange(v ?? "all")} />
        <Facet label="Repos" rows={uniqRepos(items.map((i) => i.repo))} value={repo} onPick={setRepo} />
        <Facet
          label="Template"
          rows={uniq(items.map((i) => i.chain_template))}
          value={tpl}
          onPick={setTpl}
        />
        <div className="board-foot">aggregated from worker_session usage</div>
      </aside>

      <div className="analytics-body">
        <div className="analytics-head">
          <h2>Analytics</h2>
          <span className="analytics-scope">{scope}</span>
        </div>

        {error && <p className="form-error">{error}</p>}
        {!report && !error && <p className="empty">loading…</p>}

        {t && (
          <>
            <div className="kpis">
              <Kpi
                label="Work items"
                value={String(t.work_items)}
                sub={
                  Object.entries(t.by_status)
                    .map(([k, n]) => `${n} ${k.replace("_", " ")}`)
                    .join(" · ") || "none yet"
                }
              />
              <Kpi
                label="MRs merged"
                value={String(t.mrs_merged)}
                sub={`${t.capped_out} session${t.capped_out === 1 ? "" : "s"} capped out`}
              />
              <Kpi
                label="Wall time"
                value={elapsed(t.wall_ms)}
                sub={`+ ${elapsed(t.human_wait_ms)} waiting on people`}
              />
              <Kpi
                label="Tokens"
                value={tokens(t.tokens_in + t.tokens_out)}
                sub={`${tokens(t.tokens_in)} in · ${tokens(t.tokens_out)} out`}
              />
              <Kpi
                label="Cost"
                value={usd(t.cost_usd, t.cost_complete)}
                sub={
                  !t.cost_complete
                    ? "a floor — some runs reported no cost"
                    : t.work_items_run
                      ? `${usd(t.cost_usd / t.work_items_run)} per work item run`
                      : "nothing spent yet"
                }
              />
              <Kpi
                label="Rounds"
                value={String(t.rounds)}
                sub="fix cycles across every loop"
              />
            </div>

            <section className="chart">
              <div className="chart-head">
                <span className="chart-title">Merged per week</span>
                <span className="chart-note">this week is partial</span>
              </div>
              {weeks.every((w) => w.n === 0) ? (
                <p className="empty chart-empty">no merge requests merged in this range</p>
              ) : (
                <>
              <div className="bars">
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
              <div className="bar-labels">
                {weeks.map((w) => (
                  <span key={w.label}>{w.label}</span>
                ))}
              </div>
                </>
              )}
            </section>

            <div className="analytics-tables">
              <section className="table-block by-node">
                <div className="table-title">By node</div>
                <div className="node-row node-head">
                  <span>node</span>
                  <span>cost share</span>
                  <span>runs</span>
                  <span>avg time</span>
                  <span>tokens</span>
                  <span>cost</span>
                  <span>rounds</span>
                </div>
                {report.by_node.map((n) => (
                  <div key={n.node} className="node-row" data-node={n.node}>
                    <span className="node-name" data-label="node">
                      {n.node}
                    </span>
                    <span className="share" data-label="cost share">
                      <span style={{ width: `${(n.cost_usd / topCost) * 100}%` }} />
                    </span>
                    <span className="num" data-label="runs">
                      {n.runs}
                    </span>
                    <span className="num" data-label="avg time">
                      {elapsed(n.avg_ms)}
                    </span>
                    <span className="num" data-label="tokens">
                      {tokens(n.tokens)}
                    </span>
                    <span className="num strong" data-label="cost">
                      {usd(n.cost_usd, n.cost_complete)}
                    </span>
                    <span className="num" data-label="rounds">
                      {n.rounds}
                    </span>
                  </div>
                ))}
              </section>

              <section className="table-block by-repo">
                <div className="table-title">By repo</div>
                <div className="repo-row repo-head">
                  <span>repo</span>
                  <span>items</span>
                  <span>MRs</span>
                  <span>tokens</span>
                  <span>cost</span>
                </div>
                {report.by_repo.map((r) => (
                  <div key={r.repo} className="repo-row" data-repo={r.repo}>
                    <span title={r.repo} data-label="repo">
                      {repoName(r.repo)}
                    </span>
                    <span className="num" data-label="items">
                      {r.items}
                    </span>
                    <span className="num" data-label="MRs">
                      {r.mrs}
                    </span>
                    <span className="num" data-label="tokens">
                      {tokens(r.tokens)}
                    </span>
                    <span className="num strong" data-label="cost">
                      {usd(r.cost_usd, r.cost_complete)}
                    </span>
                  </div>
                ))}
                <p className="table-foot">
                  Rounds are fix cycles. Wall time counts waits on a human separately. Cost is
                  what each agent reported it was billed — Kraft never estimates one, so a
                  trailing <b>+</b> means some run reported none and the figure is a floor.
                </p>
              </section>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
