import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import * as api from "../../api";
import { SectionLabel, Segmented, Switch } from "../../components/ui";
import { ShortId } from "../../components/ShortId";
import { adapterOf, ago } from "../../format";
import type { HookBinding, HookRun, TemplateSummary } from "../../types";
import "./plugins.css";
import { PageHead, PhoneHeader, useResource, usePhone } from "./shared";

/* ── 5c plugins (design 28, phone m14 hook list → binding) ───────────────── */

function templatesUsingHook(templates: TemplateSummary[], hook: string) {
  return templates
    .filter((t) => t.nodes.some((n) => (n.tasks ?? []).includes(hook)))
    .map((t) => t.id);
}

export function PluginsPage() {
  const { value, reload } = useResource(() => api.getRegistry());
  const { value: reposValue } = useResource(() => api.getRepos());
  const { value: templatesValue } = useResource(() => api.getTemplates());
  const [draft, setDraft] = useState<Record<string, HookBinding> | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [runs, setRuns] = useState<HookRun[]>([]);
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState("");
  // Raw text of the command input, kept separate from `draft` so a keystroke
  // never round-trips through split/join (that strips a trailing space and
  // makes a multi-word command impossible to type). Committed to `draft` on
  // blur, `null` when the field should read straight off `binding.command`.
  const [commandText, setCommandText] = useState<string | null>(null);
  const phone = usePhone();
  const hooks = draft ?? value?.hooks ?? {};
  const repos = reposValue?.repos ?? [];
  const templates = useMemo(() => templatesValue ?? [], [templatesValue]);
  const dirty = draft !== null;

  const hookIds = Object.keys(hooks).filter((h) => h.includes(filter));
  const selectedHook = params.get("hook") ?? (phone ? null : hookIds[0]);
  const binding = selectedHook ? hooks[selectedHook] : null;

  useEffect(() => {
    if (!selectedHook) return;
    setCommandText(null);
    api
      .getHookRuns(selectedHook)
      .then((r) => setRuns(r.runs))
      .catch(() => setRuns([]));
  }, [selectedHook]);

  const set = (patch: Partial<HookBinding>) => {
    if (!selectedHook) return;
    setDraft({ ...hooks, [selectedHook]: { ...hooks[selectedHook], ...patch } });
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setMessage(null);
    try {
      const { invalid_templates } = await api.putRegistry(draft);
      setDraft(null);
      await reload();
      const broken = Object.keys(invalid_templates);
      setMessage(broken.length ? `saved · now unresolvable: ${broken.join(", ")}` : "saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const list = (
    <>
      <input
        className="input"
        placeholder="filter hooks…"
        aria-label="filter hooks"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      <div className="hook-row hook-head">
        <span>Hook → binding</span>
        <span>Kind</span>
        <span>Steer</span>
      </div>
      {hookIds.map((hook) => (
        <div
          key={hook}
          className="hook-row"
          data-hook={hook}
          data-selected={hook === selectedHook || undefined}
          role="button"
          tabIndex={0}
          onClick={() => setParams({ hook })}
        >
          <span className="hook-name">{hook}</span>
          <span className="row-sub">{adapterOf(hooks[hook])}</span>
          <span onClick={(e) => e.stopPropagation()}>
            <Switch
              checked={!!hooks[hook].interactive}
              disabled={hooks[hook].kind === "subprocess"}
              title={hooks[hook].kind === "subprocess" ? "a subprocess has nothing to steer" : undefined}
              onChange={(next) => setDraft({ ...hooks, [hook]: { ...hooks[hook], interactive: next } })}
              label={`steerable: ${hook}`}
            />
          </span>
        </div>
      ))}
      <p className="settings-foot">One binding per hook in v1.</p>
    </>
  );

  const detail = selectedHook && binding && (
    <div className="plugin-detail">
      {!phone && (
        <div className="settings-head">
          <h2>{selectedHook}</h2>
          <span className="settings-note">
            used by {templatesUsingHook(templates, selectedHook).join(", ") || "no templates"}
          </span>
          <button className="btn btn-secondary" disabled title="not built yet — see the bead">
            ▷ Dry run
          </button>
        </div>
      )}
      <SectionLabel>Binding</SectionLabel>
      <div className="field">
        <label id="plugin-kind-label">kind</label>
        <Segmented
          labelledBy="plugin-kind-label"
          options={[
            { id: "builtin", label: "builtin" },
            { id: "subprocess", label: "subprocess" },
            { id: "agent", label: "agent" },
            { id: "forge", label: "forge" },
          ]}
          value={binding.kind}
          onChange={() => {}}
          disabled
        />
      </div>
      {(binding.kind === "agent" || binding.kind === "subprocess") && (
        <div className="field">
          <label htmlFor="plugin-command">command</label>
          <input
            id="plugin-command"
            className="input mono"
            value={
              commandText ??
              (Array.isArray(binding.command) ? binding.command.join(" ") : (binding.command ?? ""))
            }
            onChange={(e) => setCommandText(e.target.value)}
            onBlur={() => {
              if (commandText === null) return;
              // agent hooks need a string command; subprocess needs a list —
              // the registry (templates.load_registry) rejects the other shape.
              set({
                command:
                  binding.kind === "subprocess"
                    ? commandText.trim().split(/\s+/).filter(Boolean)
                    : commandText,
              });
              setCommandText(null);
            }}
          />
        </div>
      )}
      {binding.kind !== "builtin" && binding.timeout != null && (
        <div className="field">
          <label htmlFor="plugin-timeout">timeout</label>
          <input
            id="plugin-timeout"
            type="number"
            className="input"
            min={1}
            placeholder="minutes"
            value={binding.timeout}
            disabled
            title="not read by the executor yet — see Kraft-vpyi"
          />
        </div>
      )}
      <div className="field">
        <Switch
          checked={!!binding.interactive}
          disabled={binding.kind === "subprocess"}
          title={binding.kind === "subprocess" ? "a subprocess has nothing to steer" : undefined}
          onChange={(next) => set({ interactive: next })}
          label="steerable"
        />
      </div>

      <SectionLabel>Per repo</SectionLabel>
      <p className="settings-note">
        not read by the executor yet — see Kraft-vpyi. Shown read-only until it lands.
      </p>
      {repos.map((r) => {
        const override = binding.repos?.[r.path];
        return (
          <div key={r.path} className="plugin-repo-row" data-repo={r.path}>
            <span className="row-sub">{r.name}</span>
            <Switch
              checked={override?.enabled ?? true}
              onChange={() => {}}
              disabled
              label={`enable ${selectedHook} for ${r.name}`}
            />
            <input
              className="input mono"
              placeholder="inherit"
              value={
                Array.isArray(override?.command)
                  ? override.command.join(" ")
                  : (override?.command ?? "")
              }
              disabled
            />
          </div>
        );
      })}

      <SectionLabel>Last runs</SectionLabel>
      {runs.length === 0 && <p className="empty">no runs yet</p>}
      {runs.map((run, i) => (
        <div key={i} className="plugin-run-row">
          <span className="row-sub">
            <ShortId id={run.work_item_id} /> · {run.node_id} · cycle {run.round}
          </span>
          <span className="row-sub plugin-run-result">
            {run.wall_ms != null ? `${(run.wall_ms / 1000).toFixed(1)}s` : "—"} · {run.status}
            {run.created_at && ` · ${ago(run.created_at)}`}
          </span>
        </div>
      ))}

      <p className="save-hint">
        {message ?? "writes registry.yaml · validator re-runs · affects intake only"}
      </p>
    </div>
  );

  if (phone) {
    if (!selectedHook) {
      return (
        <>
          <PhoneHeader
            back="Settings"
            backTo="/settings"
            title="Plugins"
            subtitle={`${hookIds.length} hooks`}
            action={
              <button className="btn btn-primary" disabled={busy || !dirty} onClick={save}>
                Save
              </button>
            }
          />
          {list}
        </>
      );
    }
    return (
      <>
        <PhoneHeader
          back="Plugins"
          backTo="/settings/plugins"
          title={selectedHook}
          action={
            <button className="btn btn-primary" disabled={busy || !dirty} onClick={save}>
              Save
            </button>
          }
        />
        {detail}
      </>
    );
  }

  return (
    <>
      <PageHead
        title="Plugins"
        note="one binding per hook in v1"
        action={
          <div className="save-row">
            <button className="btn btn-ghost" disabled={!dirty} onClick={() => setDraft(null)}>
              Revert
            </button>
            <button className="btn btn-primary" disabled={busy || !dirty} onClick={save}>
              Save
            </button>
          </div>
        }
      />
      <div className="template-editor plugins-editor">
        <div className="template-list">{list}</div>
        {detail}
      </div>
    </>
  );
}
