import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ago, plural } from "../../format";
import { humanSize } from "../../sizes";
import type { StorageUsage } from "../../types";
import { detailOf, request } from "../http";
import { Button } from "../ui/Button";
import { CleanupDialog } from "./CleanupDialog";
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
  const [checked, setChecked] = useState<Set<string>>(() => new Set());
  const [cleaning, setCleaning] = useState<string[] | null>(null);
  const refreshBtn = useRef<HTMLButtonElement>(null);
  const rescueFocus = useRef(false);
  // The newest call, and the newest one that set `measuring` (a quiet one never does).
  const latest = useRef(0);
  const loud = useRef(0);

  // `quiet`: the re-read after a clean-up. The server answers from its cache, so
  // nothing says "measuring" and Refresh stays a button focus can return to.
  // Only the latest call's answer is applied: a slow Refresh must not put back what a later read took off.
  // `measuring` is cleared by the latest call that set it, even when a quiet read has since overtaken it.
  const load = useCallback(async (refresh: boolean, quiet = false) => {
    const mine = ++latest.current;
    if (!quiet) {
      loud.current = mine;
      setMeasuring(true);
    }
    const r = await request<StorageUsage>(`/storage${refresh ? "?refresh=1" : ""}`);
    if (mine === loud.current) setMeasuring(false);
    if (mine !== latest.current) return;
    // A failed read keeps the last figures: they are older, not wrong.
    if (r.status !== 200) return setError(detailOf(r.body));
    setError(null);
    setData(r.body);
    // A row that stopped being reclaimable (archived here or elsewhere) cannot stay ticked.
    const open = new Set(r.body.items.filter((i) => i.reclaimable).map((i) => i.id));
    setChecked((c) => new Set([...c].filter((id) => open.has(id))));
  }, []);
  useEffect(() => void load(false), [load]);
  // The dialog hands focus back to the button that opened it, and the re-read after
  // a clean-up then disables that button (its rows are archived): Refresh takes it.
  useEffect(() => {
    const at = document.activeElement as HTMLButtonElement | null;
    if (rescueFocus.current && (!at || at === document.body || at.disabled)) refreshBtn.current?.focus();
    rescueFocus.current = false;
  }, [data]);

  const used = data?.used_bytes ?? 0;
  const limit = data?.limit_bytes ?? null;
  // The bar runs to whichever is larger, so a figure over the limit still fits with the limit marked inside it.
  const scale = Math.max(used, limit ?? 0) || 1;
  const pct = (n: number) => `${Math.min(100, Math.round((n / scale) * 1000) / 10)}%`;

  const reclaimable = data?.items.filter((i) => i.reclaimable) ?? [];
  const toggle = (id: string) => setChecked((c) => { const n = new Set(c); if (n.has(id)) n.delete(id); else n.add(id); return n; });

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
                  <Link className="set-link" to={HOUSEKEEPING} aria-label="Edit the quota and limit in Policy › Housekeeping">Edit</Link>
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
              <Button ref={refreshBtn} disabled={measuring} onClick={() => void load(true)}>{measuring ? "Measuring…" : "Refresh"}</Button>
            </div>
          </div>
        </Block>

        {data && (
          <Block id="set-st-totals" title="Where it goes" card>
            <dl className="set-about-kv set-st-kv">
              {CATEGORIES.map(([key, label]) => (
                <Fragment key={key}>
                  <dt>{label}</dt>
                  <dd>{humanSize(data.categories[key] ?? 0)}</dd>
                </Fragment>
              ))}
            </dl>
          </Block>
        )}

        {data && (
          <Block id="set-st-worktrees" title="Worktrees" card aside={plural(data.items.length, "item")}>
            <div className="set-st-body">
              <div className="set-st-actions">
                <Button disabled={!checked.size} onClick={() => setCleaning([...checked])}>Clean up selected</Button>
                <Button disabled={!reclaimable.length} onClick={() => setCleaning(reclaimable.map((i) => i.id))}>
                  Clean up all reclaimable ({reclaimable.length}, {humanSize(data.reclaimable_bytes)})
                </Button>
                <span className="set-hint">{checked.size} selected</span>
              </div>
              {!reclaimable.length && <p className="set-hint">Nothing to clean up: no finished item holds a worktree. A running item is paused or abandoned from its own page.</p>}
              <table className="set-st-table" aria-label="Worktrees by size">
                <thead>
                  <tr>
                    <th scope="col"><span className="adr-sr">Select</span></th>
                    <th scope="col">Work item</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="is-num">Size</th>
                    <th scope="col">Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((i) => (
                    <tr key={i.id}>
                      <td>{i.reclaimable && <input type="checkbox" className="board-check" checked={checked.has(i.id)} onChange={() => toggle(i.id)} aria-label={`Select ${i.title}`} />}</td>
                      <td><Link className="set-st-title" to={`/work-items/${encodeURIComponent(i.id)}`}>{i.title}</Link></td>
                      <td>{i.archived ? "archived" : i.status.replace(/_/g, " ")}{i.reclaimable && <span className="set-st-tag"> reclaimable</span>}</td>
                      <td className="is-num">{humanSize(i.bytes)}</td>
                      <td>{ago(i.updated_at)}</td>
                    </tr>
                  ))}
                  {data.orphans.map((o) => (
                    <tr key={`orphan-${o.name}`} className="is-orphan">
                      <td />
                      <td className="set-mono">{o.name}</td>
                      <td>no work item</td>
                      <td className="is-num">{humanSize(o.bytes)}</td>
                      <td />
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.orphans.length > 0 && <p className="set-hint">A folder with no work item is listed so you can see where the space went. Kraft removes nothing here for it.</p>}
            </div>
          </Block>
        )}
        {cleaning && data && <CleanupDialog ids={cleaning} usage={data} onClose={() => setCleaning(null)} onDone={() => { rescueFocus.current = true; void load(false, true); }} />}
      </div>
    </div>
  );
}
