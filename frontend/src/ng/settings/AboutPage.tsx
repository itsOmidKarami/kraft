import { useCallback, useEffect, useState } from "react";
import * as api from "../../api";
import { ago } from "../../format";
import type { Health } from "../../types";
import { detailOf, jsonBody, request } from "../http";
import { Segmented } from "../ui/Segmented";
import { showToast } from "../ui/Toast";
import { Block, SetRow } from "./parts";
import "./settings.css";

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
    void load(false, null);
  }, [load]);

  const verdict = updateVerdict(update);
  const installed = update?.installed ?? health?.version ?? "";
  const command = `kraft admin update${update && channel && channel !== update.channel ? ` --channel ${channel}` : ""}`;
  const address = health ? `${health.bind ?? ""}${health.port != null ? `:${health.port}` : ""}` : "";
  const problems = health ? Object.keys(health.invalid_templates).length + health.invalid_policy.length : 0;
  const copy = (text: string, done: string) => navigator.clipboard?.writeText(text).then(() => showToast(done), () => showToast("Could not copy"));
  const diagnostics = [
    `kraft ${installed || "unknown"}${update ? ` (${update.channel})` : ""}`,
    `health: ${health ? health.status : "unknown"}`,
    address && `address: ${address}`,
    health && `invalid templates: ${Object.keys(health.invalid_templates).join(", ") || "none"}`,
    health && `invalid policy: ${health.invalid_policy.length ? health.invalid_policy.join("; ") : "none"}`,
    `update: ${verdict.text}`,
    `browser: ${navigator.userAgent}`,
  ].filter(Boolean).join("\n");

  return (
    <div className="ng-settings">
      <div className="set-page">
        <h1>About</h1>
        <p className="lede">This build and this instance.</p>

        <Block id="set-version" title="Version">
          <div className="set-version">
            <span className="set-version-n">{installed || "…"}</span>
            {update && <span className="set-version-channel">{update.channel}</span>}
            <span className={`set-verdict is-${verdict.tone}`} role="status">{verdict.text}</span>
          </div>
          {error && <span className="set-error" role="alert">{error}</span>}
          <span className="set-hint">Run this in a terminal, then restart Kraft. Read the release notes first: a minor release adds capabilities, a major release can change the CLI, the config schema or the state on disk.</span>
          <div className="set-command">
            <code>{command}</code>
            <button type="button" className="set-btn" onClick={() => void copy(command, "Copied the command")}>Copy</button>
          </div>
          <div className="set-inline">
            <a className="set-link" href={RELEASES} target="_blank" rel="noopener noreferrer">Release notes ↗</a>
            <span className="set-hint">{checking ? "checking…" : update?.checked_at ? `checked ${ago(update.checked_at)}` : "not checked yet"}</span>
            <button type="button" className="set-btn" disabled={checking} onClick={() => void load(true, channel)}>Check now</button>
          </div>
          <SetRow label="channel" hint="Which release feed Check now asks. The installed channel is the default.">
            <Segmented label="Channel" options={CHANNELS} value={channel ?? "stable"} onChange={(c) => { setChannel(c); void load(false, c); }} />
          </SetRow>
        </Block>

        <Block id="set-instance" title="This instance">
          <SetRow label="health" hint={problems ? "Open the Chains and Policy pages for the details." : undefined}>
            <span className={health?.status === "ok" ? "set-ok" : undefined}>{health ? (health.status === "ok" ? "ok · all chains and policy valid" : `degraded · ${problems} problem${problems === 1 ? "" : "s"}`) : "…"}</span>
          </SetRow>
          <SetRow label="address"><span>{address || "…"}</span></SetRow>
          <div>
            <button type="button" className="set-btn" onClick={() => void copy(diagnostics, "Copied the diagnostics")}>Copy diagnostics</button>
          </div>
        </Block>

        <div className="set-links">
          <a className="set-link" href={DOCS} target="_blank" rel="noopener noreferrer">Documentation ↗</a>
          <a className="set-link" href={SUPPORT} target="_blank" rel="noopener noreferrer">Status and support ↗</a>
        </div>
        <span className="set-hint">macOS and Linux. Only the latest release gets fixes.</span>
      </div>
    </div>
  );
}
