import { useCallback, useEffect, useState } from "react";
import * as api from "../../../api";
import { ago } from "../../../format";
import type { Health } from "../../../types";
import { detailOf, jsonBody, request } from "../../http";
import { showToast } from "../../ui/Toast";
import { AreaScreen } from "./AreaScreen";
import { Group, useEditor, type RowSpec } from "./kit";

interface UpdateState {
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

/** What the update feed said, in words. A feed that did not answer is "unknown", never "up to date": the server sends null, not false (W16 E). */
export function updateVerdict(u: UpdateState | null): { tone: "ok" | "warn" | "muted"; text: string } {
  if (!u) return { tone: "muted", text: "unknown" };
  if (u.latest === null || u.behind === null) return { tone: "muted", text: "unknown: the release feed did not answer" };
  return u.behind ? { tone: "warn", text: `${u.latest} is available` } : { tone: "ok", text: "up to date" };
}

/** `/settings/about` (W17 brief O.5): this build, whether a newer one exists, the update command to run in a terminal, and the instance's health. Nothing here installs anything. */
export function AboutScreen() {
  const [health, setHealth] = useState<Health | null>(null);
  const [update, setUpdate] = useState<UpdateState | null>(null);
  const [channel, setChannel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const { edit, node } = useEditor();

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
  const command = "kraft admin update";
  const address = health ? `${health.bind ?? ""}${health.port != null ? `:${health.port}` : ""}` : "";
  const held = health?.storage?.state === "held";
  const invalid = health ? Object.keys(health.invalid_templates).length + health.invalid_policy.length : 0;
  const problems = invalid + (held ? 1 : 0);
  const copy = (text: string, done: string) => navigator.clipboard?.writeText(text).then(() => showToast(done), () => showToast("Could not copy: select the text instead")) ?? showToast("Could not copy: select the text instead");
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
    <AreaScreen title="About" sub="This build and this instance." status={{ label: "read only" }} yaml={false}>
      {error && <p className="ph-error" role="alert">{error}</p>}
      <Group
        title="Version"
        rows={[
          { label: "installed", value: installed || "…", mono: true, chips: update ? [{ label: update.channel }] : undefined },
          { label: "update", value: verdict.text, chips: [{ label: verdict.tone === "ok" ? "ok" : verdict.tone === "warn" ? "available" : "unknown", tone: verdict.tone === "ok" ? "ok" : verdict.tone === "warn" ? "warn" : undefined }] },
          { label: "Check now", disabled: checking, sub: checking ? "checking…" : update?.checked_at ? `checked ${ago(update.checked_at)}` : "not checked yet", onClick: () => void load(true, channel) },
          { label: "channel", value: channel ?? "stable", sub: "Which release feed Check now asks. The installed channel is the default.", onEdit: () => edit({ kind: "choice", title: "Channel", value: channel ?? "stable", options: CHANNELS, set: async (c) => { setChannel(c); await load(false, c); return null; } }) },
        ]}
      />
      <section className="ph-group-block" aria-label="Update command">
        <div className="ph-block-head"><h2>Update command</h2></div>
        <p className="ph-note">Run this in a terminal, then restart Kraft. Read the release notes first: a minor release adds capabilities, a major release can change the CLI, the config schema or the state on disk.</p>
        <code className="ph-command" tabIndex={0}>{command}</code>
      </section>
      <Group rows={[{ label: "Copy the command", onClick: () => void copy(command, "Copied the command") }, { label: "Release notes", sub: "Opens GitHub", onClick: () => window.open(RELEASES, "_blank", "noopener,noreferrer") }]} />
      <Group
        title="This instance"
        rows={[
          { label: "health", value: health ? (health.status === "ok" ? "ok" : `degraded · ${problems}`) : "…", sub: health?.status === "ok" ? "all chains and policy valid" : held ? "Worktrees are over the storage limit: starts are held." : invalid ? "Open the Chains and Policy screens for the details." : undefined },
          { label: "address", value: address || "…", mono: true },
          { label: "Copy diagnostics", onClick: () => void copy(diagnostics, "Copied the diagnostics") },
        ]}
      />
      <Group rows={[
        { label: "Documentation", onClick: () => window.open(DOCS, "_blank", "noopener,noreferrer") } as RowSpec,
        { label: "Status and support", sub: "macOS and Linux. Only the latest release gets fixes.", onClick: () => window.open(SUPPORT, "_blank", "noopener,noreferrer") } as RowSpec,
      ]} />
      {node}
    </AreaScreen>
  );
}
