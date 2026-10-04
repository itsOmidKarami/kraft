import { useCallback, useEffect, useState, type ReactNode } from "react";
import { BookOpen, Copy, ExternalLink, LifeBuoy } from "lucide-react";
import * as api from "../../api";
import { ago, elapsed } from "../../format";
import type { Access, Health } from "../../types";
import { detailOf, jsonBody, request } from "../http";
import { Segmented } from "../ui/Segmented";
import { showToast } from "../ui/Toast";
import "./settings.css";
import { installedOlder, olderServer, restartPending } from "../shell/health";

export interface UpdateState {
  installed: string;
  latest: string | null;
  channel: string;
  behind: boolean | null;
  checked_at: string | null;
}
const CHANNELS = ["stable", "rc", "beta", "alpha"].map((c) => ({ value: c, label: c }));
const RELEASES = "https://github.com/itsOmidKarami/kraft/releases";
const DOCS = "https://itsomidkarami.github.io/kraft/";
const SUPPORT = "https://itsomidkarami.github.io/kraft/project/status-and-support";

/** What the update feed said, in words. A feed that did not answer is "unknown": it
 *  is never read as "up to date" (the server sends null, not false). */
export function updateVerdict(u: UpdateState | null): { tone: "ok" | "warn" | "muted"; text: string } {
  if (!u) return { tone: "muted", text: "unknown" };
  if (u.latest === null || u.behind === null) return { tone: "muted", text: "unknown: the release feed did not answer" };
  return u.behind ? { tone: "warn", text: `${u.latest} is available` } : { tone: "ok", text: "up to date" };
}

/** Settings › About (UX V2 W16 E): this build, whether a newer one exists, and
 *  the instance's own health. Nothing here installs anything: the page names
 *  the command to run in a terminal. */
export function AboutPage() {
  const [health, setHealth] = useState<Health | null>(null);
  const [access, setAccess] = useState<Access | null>(null);
  const [update, setUpdate] = useState<UpdateState | null>(null);
  const [channel, setChannel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const load = useCallback(async (check: boolean, ch: string | null) => {
    setError(null);
    setChecking(check);
    const q = ch ? `?channel=${encodeURIComponent(ch)}` : "";
    const r = await request<UpdateState>(`/update${check ? "/check" : ""}${q}`, check ? jsonBody("POST") : undefined);
    setChecking(false);
    if (r.status === 200) {
      setUpdate(r.body);
      setChannel(r.body.channel);
    } else {
      setUpdate(null);
      setError(detailOf(r.body));
    }
  }, []);
  useEffect(() => {
    api.getHealth().then(setHealth, () => {});
    api.getAccess().then(setAccess, () => {});
    void load(false, null);
  }, [load]);

  const verdict = updateVerdict(update);
  const installed = update?.installed ?? health?.version ?? "";
  const command = `kraft admin update${update && channel && channel !== update.channel ? ` --channel ${channel}` : ""}`;
  const address = health ? `${health.bind ?? ""}${health.port != null ? `:${health.port}` : ""}` : "";
  const process = health?.pid != null ? `pid ${health.pid}${health.uptime_s != null ? ` · up ${elapsed(health.uptime_s * 1000)}` : ""}` : "";
  const index = health?.index ? `${health.index.documents} documents · ${health.index.last_scan_at ? `scanned ${ago(health.index.last_scan_at)}` : "not scanned yet"}${health.index.errors.length ? ` · ${health.index.errors.length} scan error${health.index.errors.length === 1 ? "" : "s"}` : ""}` : "";
  const problems = health ? Object.keys(health.invalid_templates).length + health.invalid_policy.length : 0;
  const copy = (text: string, done: string) => navigator.clipboard?.writeText(text).then(() => showToast(done), () => showToast("Could not copy"));
  const diagnostics = [
    `kraft ${installed || "unknown"}${update ? ` (${update.channel})` : ""}`,
    `health: ${health ? health.status : "unknown"}`,
    address && `address: ${address}`,
    health?.run_dir && `run dir: ${health.run_dir}`,
    process && `process: ${process}`,
    index && `index: ${index}`,
    health && `invalid templates: ${Object.keys(health.invalid_templates).join(", ") || "none"}`,
    health && `invalid policy: ${health.invalid_policy.length ? health.invalid_policy.join("; ") : "none"}`,
    `update: ${verdict.text}`,
    `browser: ${navigator.userAgent}`,
  ].filter(Boolean).join("\n");

  const healthDot = health?.status === "ok" ? "is-ok" : "is-warn";
  const indexDot = health?.index && health.index.last_scan_at && !health.index.errors.length ? "is-ok" : "is-warn";
  const row = (label: string, value: ReactNode) => (<><dt>{label}</dt><dd>{value}</dd></>);

  return (
    <div className="ng-settings">
      <div className="set-page is-cards">
        <div className="set-title"><h1>About</h1><p className="lede">This build and this instance.</p></div>

        <section className="set-about-version" aria-labelledby="set-version">
          <h2 id="set-version" className="adr-sr">Version</h2>
          <div className="set-version">
            <span className="set-version-n">{installed || "…"}</span>
            {update && <span className="set-version-channel">{update.channel}</span>}
            <span className={`set-verdict is-${verdict.tone}`} role="status">{verdict.text}</span>
          </div>
          {error && <span className="set-error" role="alert">{error}</span>}
          {installedOlder(health) ? (
            <p className="set-hint is-warn" role="status">
              An older Kraft, {health!.installed}, is installed under this {health!.version} server. If you are rolling back, stop the server and restore the database from before the upgrade before you start it: a database {health!.version} migrated will not start an older release. See <a className="set-link" href="https://itsomidkarami.github.io/kraft/get-started/install#pin-or-roll-back-a-version" target="_blank" rel="noopener noreferrer">Pin or roll back a version</a>.
            </p>
          ) : restartPending(health) && (
            <p className="set-hint is-warn" role="status">
              {olderServer(health)
                ? "This server runs a release older than the Kraft installed, too old to say which. "
                : `This server is still running ${health!.version}, and ${health!.installed} is installed. `}
              Restart it to finish the update: <code>kraft admin restart</code>
            </p>
          )}
          {/* The command only where it is the advice: not beside a rollback's, which it contradicts, and said as for later
              beside "up to date" (R11b-09). */}
          {!installedOlder(health) && (
            <>
              <p className="set-hint">{update?.behind ? "Run this in a terminal" : "To update later, run this in a terminal"}, then restart Kraft. Read the release notes first: a minor release adds capabilities, a major release can change the CLI, the config schema or the state on disk.</p>
              <div className="set-command">
                <code>{command}</code>
                <button type="button" className="set-btn is-bare" onClick={() => void copy(command, "Copied the command")}><Copy size={12} aria-hidden /> Copy</button>
              </div>
            </>
          )}
          <div className="set-about-row">
            <a className="set-link" href={RELEASES} target="_blank" rel="noopener noreferrer">Release notes ↗</a>
            <span className="set-about-gap" />
            <span className="set-hint">{checking ? "checking…" : update?.checked_at ? `checked ${ago(update.checked_at)}` : "not checked yet"}</span>
            <button type="button" className="set-textbtn" disabled={checking} onClick={() => void load(true, channel)}>Check now</button>
          </div>
          <div className="set-about-row" title="Which release feed Check now asks. The installed channel is the default.">
            <span className="set-hint">Channel</span>
            <Segmented label="Channel" options={CHANNELS} value={channel ?? "stable"} onChange={(c) => { setChannel(c); void load(false, c); }} />
          </div>
        </section>

        <div className="set-about-head">
          <h2>This instance</h2>
          <button type="button" className="set-btn" onClick={() => void copy(diagnostics, "Copied the diagnostics")}><Copy size={12} aria-hidden /> Copy diagnostics</button>
        </div>
        <dl className="set-about-kv">
          {row("Health", <span className="set-dotted"><span className={`set-dot ${healthDot}`} aria-hidden /><span>{health ? (health.status === "ok" ? "ok · all chains and policy valid" : `degraded · ${problems} problem${problems === 1 ? "" : "s"}`) : "…"}</span></span>)}
          {problems > 0 && row("", <span className="set-hint">Open the Chains and Policy pages for the details.</span>)}
          {row("Address", <><span className="set-mono">{address || "…"}</span>{access && (access.auth_required ? " · sign-in on" : " · sign-in off on localhost")}</>)}
          {health?.run_dir && row("Run directory", <span className="set-mono">{health.run_dir}</span>)}
          {process && row("Process", <span className="set-mono">{process}</span>)}
          {index && row("Search index", <span className="set-dotted"><span className={`set-dot ${indexDot}`} aria-hidden /><span className={health?.index?.errors.length ? "set-warn" : undefined}>{index}</span></span>)}
        </dl>

        <div className="set-about-links">
          <a className="set-linkrow" href={DOCS} target="_blank" rel="noopener noreferrer"><BookOpen size={16} aria-hidden /><span>Documentation</span><ExternalLink size={14} aria-hidden /></a>
          <a className="set-linkrow" href={SUPPORT} target="_blank" rel="noopener noreferrer"><LifeBuoy size={16} aria-hidden /><span>Status and support</span><ExternalLink size={14} aria-hidden /></a>
        </div>
        <span className="set-hint">macOS and Linux{health?.python && ` · Python ${health.python}`} · Only the latest release gets fixes.</span>
      </div>
    </div>
  );
}
