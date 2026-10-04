import { useEffect, useMemo, useState } from "react";
import * as api from "../../api";
import { elapsed, repoName, tokens, usd } from "../../format";
import { useStore } from "../../store";
import type { Analytics } from "../../types";
import { chainOf } from "../board/model";
import { HeaderActions, HeaderTail } from "../shell/HeaderActions";
import { Menu } from "../ui/Menu";
import { weekBuckets } from "./weeks";
import "./analytics.css";

function Section({ id, title, note, bare, children }: { id: string; title: string; note?: string; bare?: boolean; children: React.ReactNode }) {
  return (
    <section className="an-section" aria-labelledby={id}>
      <div className="an-section-head">
        <h2 id={id}>{title}</h2>
        {note && <span className="an-section-note">{note}</span>}
      </div>
      {bare ? children : <div className="an-panel">{children}</div>}
    </section>
  );
}

function Kpi({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="an-kpi">
      <span className="an-kpi-label">{label}</span>
      <span className="an-kpi-value">{value}</span>
      <span className="an-kpi-sub">{sub}</span>
    </div>
  );
}

/** Analytics (UX V2 W16 F): where the time and the money went, rolled up from
 *  recorded worker sessions and event history. Every section the shipped page
 *  has, the filters it has (repo, chain), and nothing estimated. */
export function AnalyticsPage() {
  const items = useStore((s) => Object.values(s.workItems));
  const [repo, setRepo] = useState<string | null>(null);
  const [chain, setChain] = useState<string | null>(null);
  const [report, setReport] = useState<Analytics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    useStore.getState().bootstrap().catch(() => {});
  }, []);
  useEffect(() => {
    let live = true;
    setError(null);
    api.getAnalytics({ range: "8w", repo: repo ?? undefined, template: chain ?? undefined }).then(
      (r) => live && setReport(r),
      (e) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => void (live = false);
  }, [repo, chain]);

  const weeks = useMemo(() => weekBuckets(report?.weekly_merged ?? []), [report]);
  const repos = [...new Set(items.map((i) => i.repo))].sort();
  const chains = [...new Set(items.map(chainOf))].sort();
  const count = (f: (i: (typeof items)[number]) => boolean) => String(items.filter(f).length);

  const filters = (
    <HeaderActions>
      <Menu
        label="Repo"
        triggerClass="an-menu-btn"
        trigger={<><span className="an-menu-key">Repo</span> {repo ? repoName(repo) : "all"} <span aria-hidden className="an-caret">▾</span></>}
        items={[{ label: "All repos", checked: !repo, hint: String(items.length), onSelect: () => setRepo(null) }, ...repos.map((r) => ({ label: repoName(r), checked: repo === r, hint: count((i) => i.repo === r), onSelect: () => setRepo(r) }))]}
      />
      <Menu
        label="Chain"
        triggerClass="an-menu-btn"
        trigger={<><span className="an-menu-key">Chain</span> {chain ?? "all"} <span aria-hidden className="an-caret">▾</span></>}
        items={[{ label: "All chains", checked: !chain, hint: String(items.length), onSelect: () => setChain(null) }, ...chains.map((c) => ({ label: c, checked: chain === c, hint: count((i) => chainOf(i) === c), onSelect: () => setChain(c) }))]}
      />
    </HeaderActions>
  );

  const t = report?.totals;
  const delta = t?.completed_prev != null ? t.completed - t.completed_prev : null;
  const peak = Math.max(1, ...weeks.map((w) => w.n));
  const topCost = Math.max(0.000001, ...(report?.by_node.map((n) => n.cost_usd) ?? [0]));
  const totalCost = report?.by_node.reduce((s, n) => s + n.cost_usd, 0) ?? 0;
  const topStop = report?.stop_reasons[0]?.n || 1;

  return (
    <div className="an-page">
      {/* The header crumb is not a heading: the page keeps one, unseen, for a screen reader (R14b-03). */}
      <h1 className="an-visually-hidden">Analytics</h1>
      {filters}
      <HeaderTail><span className="an-scope">· Last 8 weeks · completed work items</span></HeaderTail>
      {error && <p className="an-error" role="alert">{error}</p>}
      {!report && !error && <p className="an-empty">Loading…</p>}
      {report && t && (
        <>
          <Section id="an-overview" bare title="Overview" note="Where the time and the money went. Rolled up from recorded worker sessions and event history; nothing is estimated.">
            <div className="an-kpis">
              <Kpi
                label="Completed"
                value={String(t.completed)}
                sub={[delta == null ? "no previous period" : `${delta >= 0 ? "+" : ""}${delta} vs previous`, t.completed ? `${usd(t.cost_usd / t.completed)} each` : null].filter(Boolean).join(" · ")}
              />
              <Kpi label="Lead time" value={elapsed(t.median_lead_ms)} sub={`median create → merge · ${t.human_wait_pct}% waiting on you`} />
              <Kpi label="Cost" value={usd(t.cost_usd, t.cost_complete)} sub={`${t.fix_cycles.toFixed(1)} fix cycles per verify · ${t.fix_cycles_capped} capped · ${t.rejected_gates} rejected gates`} />
            </div>
          </Section>

          <Section id="an-throughput" title="Throughput by week" note="Merged items per week. The current week is partial.">
            {t.mrs_merged === 0 ? (
              <p className="an-empty">Nothing merged in this range.</p>
            ) : (
              <>
                <div className="an-bars" role="group" aria-label="Merged items per week">
                  {weeks.map((w) => (
                    <div key={w.start} className="an-bar-col" data-partial={w.partial || undefined} tabIndex={0} aria-label={`Week of ${w.label}${w.partial ? " (partial)" : ""}: ${w.n} merged`}>
                      <span className="an-bar-n">{w.n}</span>
                      <span className="an-bar" style={{ height: `${Math.round((w.n / peak) * 100)}%` }} />
                      <span className="an-bar-label">{w.label}</span>
                    </div>
                  ))}
                </div>
                <table className="an-visually-hidden">
                  <caption>Merged items per week</caption>
                  <thead><tr><th>Week of</th><th>Merged</th></tr></thead>
                  <tbody>{weeks.map((w) => <tr key={w.start}><td>{w.start}</td><td>{w.n}</td></tr>)}</tbody>
                </table>
              </>
            )}
            <p className="an-note">The bars count merges, not completions: a chain with no merge node completes without showing up here.</p>
          </Section>

          <Section id="an-nodes" title="By node" note="Where the time and money go.">
            {report.by_node.length === 0 ? (
              <p className="an-empty">Nothing here yet.</p>
            ) : (
              <table className="an-table an-nodes">
                <thead><tr><th>node</th><th>share of cost</th><th className="an-num">min</th><th className="an-num">tokens</th><th className="an-num">$</th><th className="an-num">%</th></tr></thead>
                <tbody>
                  {report.by_node.map((n) => (
                    <tr key={n.node} data-node={n.node}>
                      <td className="an-name" data-allow-ellipsis title={n.node}>{n.node}</td>
                      <td><span className="an-share"><span style={{ width: `${(n.cost_usd / topCost) * 100}%` }} /></span></td>
                      <td className="an-num">{Math.round(n.avg_ms / 60_000)}</td>
                      <td className="an-num">{tokens(n.tokens)}</td>
                      <td className="an-num an-strong">{usd(n.cost_usd, n.cost_complete)}</td>
                      <td className="an-num">{totalCost ? Math.round((n.cost_usd / totalCost) * 100) : 0}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="an-note">Minutes are the median per completed item. Tokens and dollars are what agents reported; a trailing "+" marks a sum that is missing a session.</p>
          </Section>

          <div className="an-pair">
            <Section id="an-repos" title="By repo">
              {report.by_repo.length === 0 ? (
                <p className="an-empty">Nothing here yet.</p>
              ) : (
                <table className="an-table an-repos">
                  <thead><tr><th>repo</th><th className="an-num">items</th><th className="an-num">done</th><th className="an-num">cost</th><th className="an-num">cycles</th></tr></thead>
                  <tbody>
                    {report.by_repo.map((r) => (
                      <tr key={r.repo} data-repo={r.repo}>
                        <td className="an-name" data-allow-ellipsis title={r.repo}>{repoName(r.repo)}</td>
                        <td className="an-num">{r.items}</td>
                        <td className="an-num">{r.done}</td>
                        <td className="an-num an-strong">{usd(r.cost_usd, r.cost_complete)}</td>
                        <td className="an-num">{r.cycles}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Section>

            <Section id="an-stops" title="Why items stopped for a person">
              {report.stop_reasons.length === 0 ? (
                <p className="an-empty">Nothing stopped for a person in this range.</p>
              ) : (
                <ul className="an-stops">
                  {report.stop_reasons.map((s) => (
                    <li key={s.label} className="an-stop" data-label={s.label}>
                      <span className="an-stop-label" data-allow-ellipsis title={s.label}>{s.label}</span>
                      <span className="an-stop-bar"><span style={{ width: `${(s.n / topStop) * 100}%` }} /></span>
                      <span className="an-num">{s.n}</span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="an-note">
                {t.unplanned_touches_per_item.toFixed(2)} unplanned touches per item · open MR → green CI {elapsed(t.open_mr_to_green_ci_ms)} median
              </p>
            </Section>
          </div>
        </>
      )}
    </div>
  );
}
