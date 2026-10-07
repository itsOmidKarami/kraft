import { ChevronDown } from "lucide-react";
import { useEffect, useState } from "react";
import * as api from "../../../api";
import { elapsed, repoName, usd } from "../../../format";
import { useStore } from "../../../store";
import type { Analytics as Report } from "../../../types";
import { ChoiceSheet, useSheet } from "../nav/Sheet";
import { RootHeader } from "../nav/ScreenHeader";
import { Block } from "../ui/Rows";
import "./analytics.css";

const RANGES = [{ value: "7d", label: "7 days" }, { value: "30d", label: "30 days" }, { value: "90d", label: "90 days" }];

/** `/analytics` (W17 brief J): four tiles, each read from one field of the report, and the node, repo and stop lists. What the API cannot feed is not drawn: no spend per day, no median gate wait, no spend delta (R66). */
export function Analytics() {
  const sheet = useSheet();
  const [range, setRange] = useState("7d");
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const running = useStore((s) => Object.values(s.workItems).filter((i) => i.display_status === "running" || i.display_status === "escalated").length);
  useEffect(() => {
    let live = true;
    setError(null);
    api.getAnalytics({ range }).then((r) => live && setReport(r), (e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => void (live = false);
  }, [range]);

  const t = report?.totals;
  const delta = t?.completed_prev != null ? t.completed - t.completed_prev : null;
  const empty = !!report && t!.completed === 0 && report.by_node.length === 0;
  return (
    <>
      <RootHeader title="Analytics">
        <span className="ph-spacer" />
        <button type="button" className="ph-pill" onClick={() => sheet.open("range")}>{RANGES.find((r) => r.value === range)!.label}<ChevronDown size={14} aria-hidden="true" /></button>
      </RootHeader>
      <div className="ph-content" tabIndex={0} role="region" aria-label="Analytics figures">
        {error && <p className="ph-error" role="alert">{error}</p>}
        {!report && !error && <div className="ph-skeleton" aria-busy="true"><span /><span /></div>}
        {report && t && (
          <>
            <div className="ph-tiles">
              <Tile label="Spent" value={usd(t.cost_usd, t.cost_complete)} sub={t.completed ? `${usd(t.cost_usd / t.completed)} spend per completed item, all spend counted` : "nothing completed yet"} />
              <Tile label="Items done" value={String(t.completed)} sub={[delta == null ? "no previous period" : `${delta >= 0 ? "+" : ""}${delta} vs previous`, `${running} running now`].join(" · ")} />
              <Tile label="Time at gates" value={elapsed(t.human_wait_ms)} sub={`completed items waited ${t.human_wait_pct}% of their lead time`} />
              <Tile label="Fix loops" value={t.fix_cycles.toFixed(1)} sub={`repairs per item that needed one · ${t.fix_cycles_capped} hit their cap`} />
            </div>
            {empty ? (
              <p className="ph-empty">Nothing has completed in this range yet.</p>
            ) : (
              <>
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
                  <p className="ph-note">Tokens are as recorded, never estimated. Dollars are what agents reported plus Kraft's estimate from its price table where they reported none, and a session it cannot price puts a "+" on the sum.</p>
                </Block>
                <Block title="By repo">
                  {report.by_repo.length === 0 ? <p className="ph-note">Nothing here yet.</p> : (
                    <div className="ph-list">
                      {report.by_repo.map((r) => (
                        <div key={r.repo} className="ph-stat-row">
                          <span className="ph-stat-name">{repoName(r.repo)}</span>
                          <span className="ph-stat-meta">{r.items} items · {r.done} done · avg repairs {r.cycles}</span>
                          <span className="ph-stat-val">{usd(r.cost_usd, r.cost_complete)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </Block>
                <Block title="Why items stopped for a person">
                  {report.stop_reasons.length === 0 ? <p className="ph-note">Nothing stopped for a person in this range.</p> : (
                    <div className="ph-list">
                      {report.stop_reasons.map((s) => (
                        <div key={s.label} className="ph-stat-row">
                          <span className="ph-stat-name">{s.label}</span>
                          <span className="ph-stat-val">{s.n}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <p className="ph-note">{t.unplanned_touches_per_item.toFixed(2)} unplanned touches per item · open MR → checks done {elapsed(t.open_mr_to_green_ci_ms)} median</p>
                </Block>
              </>
            )}
          </>
        )}
      </div>
      {sheet.is("range") && <ChoiceSheet title="Range" value={range} options={RANGES} onPick={(v) => sheet.closeThen(() => setRange(v))} onClose={sheet.close} />}
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
