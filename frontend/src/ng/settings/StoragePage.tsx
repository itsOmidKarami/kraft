import { Fragment, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ago } from "../../format";
import { humanSize } from "../../sizes";
import type { StorageUsage } from "../../types";
import { detailOf, request } from "../http";
import { Button } from "../ui/Button";
import { Block } from "./parts";
import "./settings.css";
import "./storage.css";

const HOUSEKEEPING = "/settings/policy/housekeeping";
/** The spec's seven categories, in the order the page lists them. */
const CATEGORIES: [key: string, label: string][] = [
  ["worktrees", "Worktrees"],
  ["sandboxes", "Sandbox stores and homes"],
  ["logs", "Logs"],
  ["results", "Results"],
  ["databases", "Databases and backups"],
  ["attachments", "Attachments"],
  ["other", "Everything else"],
];

/** Settings › Storage: what the worktrees use against the quota and limit, and
 *  which of them a clean-up may take. The figure is the server's cached
 *  measurement; Refresh measures again, which can take half a minute. */
export function StoragePage() {
  const [data, setData] = useState<StorageUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [measuring, setMeasuring] = useState(true);

  const load = useCallback(async (refresh: boolean) => {
    setMeasuring(true);
    const r = await request<StorageUsage>(`/storage${refresh ? "?refresh=1" : ""}`);
    setMeasuring(false);
    // A failed read keeps the last figures: they are older, not wrong.
    if (r.status !== 200) return setError(detailOf(r.body));
    setError(null);
    setData(r.body);
  }, []);
  useEffect(() => void load(false), [load]);

  const used = data?.used_bytes ?? 0;
  const limit = data?.limit_bytes ?? null;
  // The bar runs to whichever is larger, so a figure over the limit still fits with the limit marked inside it.
  const scale = Math.max(used, limit ?? 0) || 1;
  const pct = (n: number) => `${Math.min(100, Math.round((n / scale) * 1000) / 10)}%`;

  // One live region, always mounted: a screen reader hears a change in it, not a relabelled button.
  const note =
    measuring && data ? "Measuring…" : data?.state === "held" ? "Over the limit: starts are held until you make room." : data?.state === "over_quota" ? "Over the quota: Kraft is warning; starts still go ahead." : "";

  return (
    <div className="ng-settings">
      <div className="set-page is-cards">
        <div className="set-title">
          <h1>Storage</h1>
          <p className="lede">What the worktrees use, and what removing the finished ones would free.</p>
        </div>

        <Block id="set-st-usage" title="Usage" card aside={data ? `measured ${ago(data.measured_at)}` : undefined}>
          <div className="set-st-body">
            {error && <p className="set-error" role="alert">{error}</p>}
            {!data && measuring && <p className="set-hint">Measuring the run folder. This can take half a minute on a large instance.</p>}
            {data && limit != null && data.state ? (
              <>
                <div className={`set-st-bar is-${data.state}`} role="img" aria-label={`Worktrees use ${humanSize(used)} of the ${humanSize(limit)} limit`}>
                  <div className="set-st-fill" style={{ width: pct(used) }} />
                  <span className="set-st-mark" style={{ left: pct(data.quota_bytes ?? 0) }} />
                  <span className="set-st-mark" style={{ left: pct(limit) }} />
                </div>
                <p className="set-hint">
                  {humanSize(used)} used · quota {humanSize(data.quota_bytes ?? 0)} · limit {humanSize(limit)}{" "}
                  <Link className="set-link" to={HOUSEKEEPING}>Change them</Link>
                </p>
              </>
            ) : (
              data && (
                <p className="set-hint">
                  {humanSize(used)} used by worktrees · no limit set.{" "}
                  <Link className="set-link" to={HOUSEKEEPING}>Set one in Policy › Housekeeping</Link>
                </p>
              )
            )}
            <p className={`set-st-note${!measuring && data?.state === "held" ? " is-held" : !measuring && data?.state === "over_quota" ? " is-warn" : ""}`} role="status">{note}</p>
            <div className="set-st-actions">
              <Button disabled={measuring} onClick={() => void load(true)}>{measuring ? "Measuring…" : "Refresh"}</Button>
            </div>
          </div>
        </Block>

        {data && (
          <Block id="set-st-totals" title="Where it goes" card>
            <dl className="set-about-kv">
              {CATEGORIES.map(([key, label]) => (
                <Fragment key={key}>
                  <dt>{label}</dt>
                  <dd>{humanSize(data.categories[key] ?? 0)}</dd>
                </Fragment>
              ))}
            </dl>
          </Block>
        )}
      </div>
    </div>
  );
}
