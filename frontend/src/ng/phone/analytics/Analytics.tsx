import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../../../api";
import { elapsed, repoName, usd } from "../../../format";
import { useStore } from "../../../store";
import type { Analytics as Report } from "../../../types";
import { boardHref, DEFAULT_RANGE, gateRows, HOW_COUNTED, measured, RANGES, EMPTY_RANGE, waitingKpi } from "../../analytics/shared";
import { chainOf } from "../../board/model";
import { ChoiceSheet, useSheet } from "../nav/Sheet";
import { RootHeader } from "../nav/ScreenHeader";
import { Block } from "../ui/Rows";
import "./analytics.css";

/** `/analytics` (W17 brief J): four tiles, each read from one field of the report, and the gate, node, repo and stop lists. The range, repo and chain choices are the desktop's. What the API cannot feed is not drawn: no spend per day, no spend delta (R66). */
export function Analytics() {
  const sheet = useSheet();
  const [range, setRange] = useState(DEFAULT_RANGE);
  const [repo, setRepo] = useState("");
  const [chain, setChain] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const items = useStore((s) => Object.values(s.workItems));
  useEffect(() => {
    let live = true;
    setError(null);
    api.getAnalytics({ range, repo: repo || undefined, template: chain || undefined }).then((r) => live && setReport(r), (e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => void (live = false);
  }, [range, repo, chain]);

  const t = report?.totals;
  const delta = t?.completed_prev != null ? t.completed - t.completed_prev : null;
  const repos = [...new Set(items.map((i) => i.repo))].sort((a, b) => repoName(a).localeCompare(repoName(b)));
  const chains = [...new Set(items.map(chainOf))].sort();
  const scopeHref = boardHref({ repo });
  const gates = report ? gateRows(report) : [];
  const empty = !!report && t!.completed === 0 && report.by_node.length === 0;
  return (
    <>
      <RootHeader title="Analytics" />
      <div className="ph-chips" role="group" aria-label="Filters">
        <button type="button" className="ph-pill" onClick={() => sheet.open("range")}>{RANGES.find((r) => r.value === range)!.label}<ChevronDown size={14} aria-hidden="true" /></button>
        <button type="button" className="ph-pill" onClick={() => sheet.open("repo")}>{repo ? repoName(repo) : "All repos"}<ChevronDown size={14} aria-hidden="true" /></button>
        <button type="button" className="ph-pill" onClick={() => sheet.open("chain")}>{chain || "All chains"}<ChevronDown size={14} aria-hidden="true" /></button>
      </div>
      <div className="ph-content" tabIndex={0} role="region" aria-label="Analytics figures">
        {error && <p className="ph-error" role="alert">{error}</p>}
        {!report && !error && <div className="ph-skeleton" aria-busy="true"><span /><span /></div>}
        {report && t && (
          <>
            {empty ? (
              <p className="ph-empty">{EMPTY_RANGE}</p>
            ) : (
              <>
                <div className="ph-tiles">
                <Tile label="Completed" value={String(t.completed)} sub={delta == null ? "no previous period" : `${delta >= 0 ? "+" : ""}${delta} vs previous`} />
                <Tile label="Lead time" value={measured(t.median_lead_ms, t.completed > 0)} sub="median" />
                <Tile {...waitingKpi(t.human_wait_pct)} />
                <Tile label="Cost" value={usd(t.cost_usd, t.cost_complete)} sub={t.completed ? `${usd(t.cost_usd / t.completed)} per completed item` : "nothing completed yet"} />
                <Tile label="Repairs per item" value={t.fix_cycles.toFixed(1)} sub={t.fix_cycles_capped ? `${t.fix_cycles_capped} hit their cap` : "none hit their cap"} />
                </div>

                <Block title="Waiting at each gate">
                  {gates.length === 0 ? <p className="ph-note">No gate waits in this range.</p> : (
                    <div className="ph-list">
                      {gates.map((g) => (
                        <Link key={g.gate} to={scopeHref} className="ph-stat-row ph-stat-link">
                          <span className="ph-stat-name ph-mono">{g.gate}</span>
                          <span className="ph-stat-meta">{g.n} {g.n === 1 ? "wait" : "waits"} · median {elapsed(g.median_ms)}{g.rejected ? ` · ${g.rejected} rejected` : ""}</span>
                          <span className="ph-stat-val">{elapsed(g.total_ms)}<ChevronRight size={14} aria-hidden="true" /></span>
                        </Link>
                      ))}
                    </div>
                  )}
                </Block>
                <Block title="By node">
                  {report.by_node.length === 0 ? <p className="ph-note">Nothing here yet.</p> : (
                    <div className="ph-list">
                      {report.by_node.map((n) => (
                        <div key={n.node} className="ph-stat-row">
                          <span className="ph-stat-name ph-mono">{n.node}</span>
                          <span className="ph-stat-meta">{n.runs} {n.runs === 1 ? "run" : "runs"}</span>
                          <span className="ph-stat-val">{usd(n.cost_usd, n.cost_complete)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </Block>
                <Block title="By repo">
                  {report.by_repo.length === 0 ? <p className="ph-note">Nothing here yet.</p> : (
                    <div className="ph-list">
                      {report.by_repo.map((r) => (
                        <Link key={r.repo} to={boardHref({ repo: r.repo })} className="ph-stat-row ph-stat-link">
                          <span className="ph-stat-name">{repoName(r.repo)}</span>
                          <span className="ph-stat-meta">{r.items} items · {r.done} done · avg repairs {r.cycles.toFixed(1)}</span>
                          <span className="ph-stat-val">{usd(r.cost_usd, r.cost_complete)}<ChevronRight size={14} aria-hidden="true" /></span>
                        </Link>
                      ))}
                    </div>
                  )}
                </Block>
                <Block title="Why items stopped for a person">
                  {report.stop_reasons.length === 0 ? <p className="ph-note">Nothing stopped for a person in this range.</p> : (
                    <div className="ph-list">
                      {report.stop_reasons.map((s) => (
                        <Link key={s.label} to={scopeHref} className="ph-stat-row ph-stat-link">
                          <span className="ph-stat-name">{s.label}</span>
                          <span className="ph-stat-val">{s.n}<ChevronRight size={14} aria-hidden="true" /></span>
                        </Link>
                      ))}
                    </div>
                  )}
                  <p className="ph-note">{t.unplanned_touches_per_item.toFixed(2)} unplanned touches per item · open MR → checks done {measured(t.open_mr_to_green_ci_ms)} median</p>
                </Block>
                <details className="ph-how">
                  <summary>How this is counted</summary>
                  {HOW_COUNTED.map((line) => <p key={line} className="ph-note">{line}</p>)}
                </details>
              </>
            )}
          </>
        )}
      </div>
      {sheet.is("range") && <ChoiceSheet title="Range" value={range} options={RANGES} onPick={(v) => sheet.closeThen(() => setRange(v))} onClose={sheet.close} />}
      {sheet.is("repo") && (
        <ChoiceSheet title="Repo" value={repo} options={[{ value: "", label: "All repos", hint: String(items.length) }, ...repos.map((r) => ({ value: r, label: repoName(r), hint: String(items.filter((i) => i.repo === r).length) }))]} onPick={(v) => sheet.closeThen(() => setRepo(v))} onClose={sheet.close} />
      )}
      {sheet.is("chain") && (
        <ChoiceSheet title="Chain" value={chain} options={[{ value: "", label: "All chains", hint: String(items.length) }, ...chains.map((c) => ({ value: c, label: c, hint: String(items.filter((i) => chainOf(i) === c).length) }))]} onPick={(v) => sheet.closeThen(() => setChain(v))} onClose={sheet.close} />
      )}
    </>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="ph-tile">
      <span className="ph-tile-label">{label}</span>
      <span className="ph-tile-value">{value}</span>
      <span className="ph-tile-sub">{sub}</span>
    </div>
  );
}
