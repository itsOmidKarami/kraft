import { useEffect, useMemo, useState } from "react";
import * as api from "../../api";
import { elapsed, repoName, tokens, usd } from "../../format";
import { useStore } from "../../store";
import type { Analytics } from "../../types";
import { chainOf } from "../board/model";
import { Link } from "react-router-dom";
import { HeaderActions, HeaderTail } from "../shell/HeaderActions";
import { Menu } from "../ui/Menu";
import { boardHref, DEFAULT_RANGE, EMPTY_RANGE, gateRows, HOW_COUNTED, measured, minutes, RANGES, scopeLine, waitingKpi } from "./shared";
import { weekBuckets } from "./weeks";
import "./analytics.css";

/** A section with nothing to show keeps its heading and says so on that line, in no panel. */
function Section({ id, title, note, empty, bare, wide, foot, children }: { id: string; title: string; note?: string; empty?: string; bare?: boolean; wide?: boolean; foot?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className={`an-section${wide ? " an-wide" : ""}`} aria-labelledby={id}>
      <div className="an-section-head">
        <h2 id={id}>{title}</h2>
        {(empty ?? note) && <span className="an-section-note">{empty ?? note}</span>}
      </div>
      {empty ? null : bare ? children : <div className="an-panel">{children}</div>}
      {foot}
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
 *  has, and the filters it has (repo, chain). */
export function AnalyticsPage() {
  const items = useStore((s) => Object.values(s.workItems));
  const [repo, setRepo] = useState<string | null>(null);
  const [chain, setChain] = useState<string | null>(null);
  const [range, setRange] = useState(DEFAULT_RANGE);
  const [report, setReport] = useState<Analytics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    useStore.getState().bootstrap().catch(() => {});
  }, []);
  useEffect(() => {
    let live = true;
    setError(null);
    api.getAnalytics({ range, repo: repo ?? undefined, template: chain ?? undefined }).then(
      (r) => live && setReport(r),
      (e) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => void (live = false);
  }, [range, repo, chain]);

  const weeks = useMemo(() => weekBuckets(report?.weekly_merged ?? []), [report]);
  const repos = [...new Set(items.map((i) => i.repo))].sort();
  const chains = [...new Set(items.map(chainOf))].sort();
  const count = (f: (i: (typeof items)[number]) => boolean) => String(items.filter(f).length);

  const rangeLabel = RANGES.find((r) => r.value === range)!.label;
  const filters = (
    <HeaderActions>
      <Menu
        label="Range"
        triggerClass="an-menu-btn"
        trigger={<><span className="an-menu-key">Range</span> {rangeLabel} <span aria-hidden className="an-caret">▾</span></>}
        items={RANGES.map((r) => ({ label: r.label, checked: range === r.value, onSelect: () => setRange(r.value) }))}
      />
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
  const scopeHref = boardHref({ repo, chain });
  const gates = report ? gateRows(report) : [];
  const emptyRange = !!report && t!.completed === 0 && report.by_node.length === 0;
  const topWait = Math.max(1, ...gates.map((g) => g.total_ms));

  return (
    <div className="an-page">
      {/* The header crumb is not a heading: the page keeps one, unseen, for a screen reader (R14b-03). */}
      <h1 className="an-visually-hidden">Analytics</h1>
      {filters}
      <HeaderTail><span className="an-scope">· {scopeLine(rangeLabel)}</span></HeaderTail>
      {error && <p className="an-error" role="alert">{error}</p>}
      {!report && !error && <p className="an-empty">Loading…</p>}
      {report && t && emptyRange && <p className="an-empty">{EMPTY_RANGE}</p>}
      {report && t && !emptyRange && (
        <>
          <Section id="an-overview" bare title="Overview">
            <div className="an-kpis">
              <Kpi label="Completed" value={String(t.completed)} sub={delta == null ? "no previous period" : `${delta >= 0 ? "+" : ""}${delta} vs previous`} />
              <Kpi label="Lead time" value={measured(t.median_lead_ms, t.completed > 0)} sub="median" />
              <Kpi {...waitingKpi(t.human_wait_pct)} />
              <Kpi label="Cost" value={usd(t.cost_usd, t.cost_complete)} sub={t.completed ? `${usd(t.cost_usd / t.completed)} per completed item` : "nothing completed yet"} />
              <Kpi label="Repairs per item" value={t.fix_cycles.toFixed(1)} sub={t.fix_cycles_capped ? `${t.fix_cycles_capped} hit their cap` : "none hit their cap"} />
            </div>
          </Section>

          <div className="an-grid">
            <Section id="an-throughput" title="Throughput by week" note={`Merged items per week, always the last eight weeks, from the items created in the range. This week is partial.`} empty={t.mrs_merged === 0 ? "Nothing merged in this range." : undefined}>
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
            </Section>

            <Section id="an-gates" title="Waiting at each gate" note="Where items waited on a person." empty={gates.length === 0 ? "No gate waits in this range." : undefined}>
              <table className="an-table an-gates">
                <thead><tr><th>gate</th><th>share of wait</th><th className="an-num">waits</th><th className="an-num">median</th><th className="an-num">total</th><th className="an-num">rejected</th></tr></thead>
                <tbody>
                  {gates.map((g) => (
                    <tr key={g.gate} data-gate={g.gate}>
                      <td className="an-name" data-allow-ellipsis title={g.gate}><Link className="an-link" to={scopeHref}>{g.gate}</Link></td>
                      <td><span className="an-share"><span style={{ width: `${(g.total_ms / topWait) * 100}%` }} /></span></td>
                      <td className="an-num">{g.n}</td>
                      <td className="an-num">{elapsed(g.median_ms)}</td>
                      <td className="an-num an-strong">{elapsed(g.total_ms)}</td>
                      <td className="an-num">{g.rejected}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Section>

            <Section id="an-nodes" wide title="By node" empty={report.by_node.length === 0 ? "Nothing here yet." : undefined}>
              <table className="an-table an-nodes">
                <thead><tr><th>node</th><th>share of cost</th><th className="an-num">min</th><th className="an-num">tokens</th><th className="an-num">$</th><th className="an-num">%</th></tr></thead>
                <tbody>
                  {report.by_node.map((n) => (
                    <tr key={n.node} data-node={n.node}>
                      <td className="an-name" data-allow-ellipsis title={n.node}>{n.node}</td>
                      <td>{(n.cost_usd > 0 || n.cost_complete) && <span className="an-share"><span style={{ width: `${(n.cost_usd / topCost) * 100}%` }} /></span>}</td>
                      <td className="an-num">{minutes(n.avg_ms, n.runs)}</td>
                      <td className="an-num">{tokens(n.tokens)}</td>
                      <td className="an-num an-strong">{usd(n.cost_usd, n.cost_complete)}</td>
                      <td className="an-num">{totalCost ? Math.round((n.cost_usd / totalCost) * 100) : 0}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Section>

            <Section id="an-repos" title="By repo" empty={report.by_repo.length === 0 ? "Nothing here yet." : undefined}>
              <table className="an-table an-repos">
                <thead><tr><th>repo</th><th className="an-num">items</th><th className="an-num">done</th><th className="an-num">cost</th><th className="an-num" title="Average, over the repo's items that needed at least one repair, of the fix-loop repair rounds (round 1 and up) each ran">avg repairs</th></tr></thead>
                <tbody>
                  {report.by_repo.map((r) => (
                    <tr key={r.repo} data-repo={r.repo}>
                      <td className="an-name" data-allow-ellipsis title={r.repo}><Link className="an-link" to={boardHref({ repo: r.repo, chain })}>{repoName(r.repo)}</Link></td>
                      <td className="an-num">{r.items}</td>
                      <td className="an-num">{r.done}</td>
                      <td className="an-num an-strong">{usd(r.cost_usd, r.cost_complete)}</td>
                      <td className="an-num">{r.cycles.toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Section>

            <Section id="an-stops" title="Why items stopped for a person" foot={<p className="an-note">{t.unplanned_touches_per_item.toFixed(2)} unplanned touches per item · open MR → checks done {measured(t.open_mr_to_green_ci_ms)} median</p>} empty={report.stop_reasons.length === 0 ? "Nothing stopped for a person in this range." : undefined}>
              <ul className="an-stops">
                {report.stop_reasons.map((s) => (
                  <li key={s.label} className="an-stop" data-label={s.label}>
                    <Link className="an-stop-label an-link" data-allow-ellipsis title={s.label} to={scopeHref}>{s.label}</Link>
                    <span className="an-stop-bar"><span style={{ width: `${(s.n / topStop) * 100}%` }} /></span>
                    <span className="an-num">{s.n}</span>
                  </li>
                ))}
              </ul>
            </Section>
          </div>

          <details className="an-how">
            <summary>How this is counted</summary>
            {HOW_COUNTED.map((line) => <p key={line} className="an-note">{line}</p>)}
          </details>
        </>
      )}
    </div>
  );
}
