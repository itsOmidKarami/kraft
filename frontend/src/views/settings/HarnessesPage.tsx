import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import * as api from "../../api";
import { SectionLabel, Switch } from "../../components/ui";
import type { HarnessProfile, HarnessProfileInput, HarnessProvider } from "../../types";
import "./templates.css";
import { PageHead, PhoneHeader, SaveRow, usePhone, useResource } from "./shared";

/* ── Harnesses (Kraft-archr): `templates/harnesses.yaml`, the profiles an agent
 * task's `harness:` selects -- which provider CLI runs, from which executable,
 * with which defaults.
 *
 * The list is `GET /harnesses/profiles`: every profile with the library tasks and chains
 * that select it. A profile is edited as a form and saved on its own
 * (`PUT /harnesses/profiles/{id}`); the server refuses, naming why, what its loader
 * refuses or what would stop a resolving chain's agent task launching. Each
 * provider's capability surface (`GET /harnesses/providers`) is read-only: it
 * is what a CLI accepts, not a setting. */

/** The profile defaults a launch applies (`agent._PROFILE_DEFAULTS`). */
const DEFAULT_KEYS = ["model", "effort", "permission_mode"] as const;

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function Links({ ids, to, none }: { ids: string[]; to: (id: string) => string; none: string }) {
  if (ids.length === 0) return <>{none}</>;
  return (
    <>
      {ids.map((id, i) => (
        <span key={id}>
          {i > 0 && ", "}
          <Link to={to(id)}>{id}</Link>
        </span>
      ))}
    </>
  );
}

function ProviderReadout({ id, provider }: { id: string; provider: HarnessProvider | undefined }) {
  if (!provider) return <p className="form-error">provider {id} is not installed</p>;
  return (
    <>
      <div className="chain-head">
        <h2 className="chain-head-name">provider {id}</h2>
        <span className="chain-head-counts">{provider.command.join(" ")}</span>
      </div>
      <span className="field-hint">{provider.path}</span>
      <pre className="template-readout">
        {Object.entries(provider.capabilities)
          .map(([name, c]) =>
            [name, c.values.length ? `values: ${c.values.join(" | ")}` : "", c.always ? `always: ${c.always}` : ""]
              .filter(Boolean)
              .join("  "),
          )
          .join("\n")}
      </pre>
    </>
  );
}

function ProfileEditor({
  profile,
  providers,
  onSaved,
}: {
  profile: HarnessProfile;
  providers: Record<string, HarnessProvider>;
  onSaved: () => Promise<void>;
}) {
  const saved: HarnessProfileInput = {
    provider: profile.provider,
    enabled: profile.enabled,
    executable: profile.executable ?? "",
    defaults: profile.defaults,
  };
  const [draft, setDraft] = useState(saved);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const provider = providers[draft.provider];
  const set = (patch: Partial<HarnessProfileInput>) => setDraft({ ...draft, ...patch });
  const setDefault = (key: string, value: string) => {
    const { [key]: _, ...rest } = draft.defaults;
    set({ defaults: value ? { ...rest, [key]: value } : rest });
  };

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const { executable, ...body } = draft;
      await api.putHarness(profile.id, executable ? { ...body, executable } : body);
      await onSaved();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="chain-head">
        <h2 className="chain-head-name">{profile.id}</h2>
        <span className="chain-head-counts">{profile.enabled ? profile.provider : "disabled"}</span>
      </div>
      <p className="chain-legend">
        used by{" "}
        <Links ids={profile.used_by} to={(id) => `/settings/library?c=${encodeURIComponent(id)}`} none="no library task" />
        {" · chains "}
        <Links ids={profile.chains} to={(id) => `/settings/chains?tpl=${encodeURIComponent(id)}`} none="none" />
      </p>
      <div className="field">
        <Switch
          checked={draft.enabled}
          onChange={(enabled) => set({ enabled })}
          label={`${draft.enabled ? "disable" : "enable"} ${profile.id}`}
        />
        <span className="field-hint">
          {draft.enabled ? "enabled" : "disabled — a task selecting it stops for a human"}
        </span>
      </div>
      <div className="field">
        <label htmlFor="harness-provider">provider</label>
        <select
          id="harness-provider"
          className="input"
          value={draft.provider}
          onChange={(e) => set({ provider: e.target.value })}
        >
          {[...new Set([draft.provider, ...Object.keys(providers)])].map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="harness-executable">executable</label>
        <input
          id="harness-executable"
          className="input mono"
          placeholder={provider?.command[0] ?? ""}
          value={draft.executable ?? ""}
          onChange={(e) => set({ executable: e.target.value })}
        />
      </div>
      {DEFAULT_KEYS.filter((key) => provider?.capabilities[key] || draft.defaults[key]).map((key) => {
        const values = provider?.capabilities[key]?.values ?? [];
        return (
          <div className="field" key={key}>
            <label htmlFor={`harness-${key}`}>
              default {key} <span className="field-hint">· {values.length ? values.join(" | ") : "any"}</span>
            </label>
            <input
              id={`harness-${key}`}
              className="input mono"
              value={draft.defaults[key] ?? ""}
              onChange={(e) => setDefault(key, e.target.value)}
            />
          </div>
        );
      })}
      <SaveRow
        onSave={save}
        onDiscard={() => setDraft(saved)}
        hint="writes harnesses.yaml; the next launch reads it"
        busy={busy}
        dirty={dirty}
        message={message}
      />
    </>
  );
}

export function HarnessesPage() {
  const { value, error, reload } = useResource(() => Promise.all([api.getHarnesses(), api.getHarnessProviders()]));
  const phone = usePhone();
  const [params, setParams] = useSearchParams();
  const [harnesses, providers] = value ?? [null, null];
  const profiles = harnesses?.profiles ?? [];
  const selected = profiles.find((p) => p.id === params.get("h")) ?? profiles[0] ?? null;

  return (
    <>
      {phone ? (
        <PhoneHeader back="Settings" backTo="/settings" title="Harnesses" subtitle="~/.kraft/templates/harnesses.yaml" />
      ) : (
        <PageHead title="Harnesses" note="Which agent CLI each task runs on, and with what defaults" />
      )}
      {error && <p className="form-error">{error}</p>}
      {harnesses?.error && <p className="form-error">{harnesses.error}</p>}
      <div className="chain-page">
        <div className="template-editor">
          <div className="template-list">
            <SectionLabel>profiles</SectionLabel>
            {profiles.map((p) => (
              <button
                key={p.id}
                className="facet-opt steering-row"
                aria-pressed={p.id === selected?.id}
                onClick={() => setParams({ h: p.id })}
              >
                <span className="steering-row-name">{p.id}</span>
                <span className="facet-count">
                  {p.used_by.length ? plural(p.used_by.length, "task") : "unused"}
                  {!p.enabled && " · disabled"}
                </span>
              </button>
            ))}
            {harnesses && !harnesses.error && profiles.length === 0 && <p className="empty">no profiles yet</p>}
            {providers && Object.keys(providers.invalid).length > 0 && (
              <div className="validation" data-valid={false}>
                {Object.entries(providers.invalid).map(([id, reason]) => (
                  <span key={id}>
                    {id}: {reason}
                  </span>
                ))}
              </div>
            )}
          </div>
          <div className="template-draft">
            {selected && providers && (
              <ProfileEditor key={selected.id} profile={selected} providers={providers.valid} onSaved={reload} />
            )}
          </div>
        </div>
        {selected && providers && (
          <div className="chain-yaml-pane">
            <ProviderReadout id={selected.provider} provider={providers.valid[selected.provider]} />
          </div>
        )}
      </div>
    </>
  );
}
