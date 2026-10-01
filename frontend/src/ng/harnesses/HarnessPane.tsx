import { Inspector } from "../graph/Inspector";
import type { useResizable } from "../graph/useResizable";
import { Head, Kv, Note } from "../templates/panes/controls";
import type { ConfigDraft } from "../templates/draft/useConfigDraft";
import { problemText } from "../templates/problems";
import { Segmented } from "../ui/Segmented";
import { Button } from "../ui/Button";
import { Switch } from "../ui/Switch";
import { useState } from "react";
import { ModelField } from "./fields";
import { ACCESS, type Access, type EntryView, type HarnessView, type HProblem, type Resolved } from "./model";
import { entryOp, useRun } from "./ops";
import type { ProviderStatus } from "./useProviders";

const HELP: Record<Access, string> = {
  available: "Tasks may select it. A repo can still narrow this in Repos.",
  override: "Nothing selects it by default; an item or a retry override may name it.",
  never: "No task, repo or override may use it.",
};

type Common = {
  draft: ConfigDraft;
  r: Resolved;
  providers: ProviderStatus[];
  problems: HProblem[];
  changes: Set<string>;
  open: boolean;
  size: ReturnType<typeof useResizable>;
  onCollapse: () => void;
  onExpand: () => void;
};

/** Model and effort of one provider entry of a profile (shared by the harness's lane pane and the profile's entry pane). */
export function EntryEditor({ draft, profile, provider, entry, status, changed }: {
  draft: ConfigDraft;
  profile: string;
  provider: string;
  entry: EntryView;
  status?: ProviderStatus;
  changed: boolean;
}) {
  const { run, error } = useRun(draft);
  const efforts = status?.efforts ?? [];
  return (
    <>
      <ModelField label="model" value={entry.model ?? ""} suggestions={status?.models} changed={changed} onCommit={(model) => void run(entryOp(profile, provider, { ...entry, model }))} />
      {efforts.length > 0 ? (
        <div className="hn-field">
          <span className="hn-field-label" id={`eff-${profile}-${provider}`}>effort</span>
          <Segmented
            label={`Effort for ${provider}`}
            options={[{ value: "", label: "default" }, ...efforts.map((e) => ({ value: e, label: e }))]}
            value={entry.effort ?? ""}
            onChange={(effort) => void run(entryOp(profile, provider, { model: entry.model, effort }))}
          />
          <p className="hn-field-help">What {provider} accepts.</p>
        </div>
      ) : (
        <Note>{provider} has no effort levels.</Note>
      )}
      {error && <p className="hn-error" role="alert">{error}</p>}
    </>
  );
}

/** Add a provider entry to a profile: the model first (an empty one is refused by the server). */
export function AddEntry({ draft, profile, provider, status }: { draft: ConfigDraft; profile: string; provider: string; status?: ProviderStatus }) {
  const { run, error } = useRun(draft);
  const [model, setModel] = useState(status?.models[0] ?? "");
  return (
    <div className="hn-add-entry">
      <ModelField label={`${provider} model`} value={model} suggestions={status?.models} onCommit={setModel} />
      <Button variant="primary" disabled={!model.trim()} onClick={() => void run(entryOp(profile, provider, { model: model.trim() }))}>Add {provider} entry</Button>
      {error && <p className="hn-error" role="alert">{error}</p>}
    </div>
  );
}

/** The harness's own fields (`set_harness`): enabled, executable and the defaults a launch applies when a task names none. */
function HarnessFields({ draft, h, status, changed, onYaml }: { draft: ConfigDraft; h: HarnessView; status?: ProviderStatus; changed: boolean; onYaml?: (file: string) => void }) {
  const { run, error } = useRun(draft);
  const patch = (p: Record<string, unknown>) => void run({ op: "set_harness", id: h.id, patch: p });
  const setDefault = (key: string, value: string) => patch({ defaults: { [key]: value || null } });
  const efforts = status?.efforts ?? [];
  const modes = status?.capabilities.permission_mode?.values ?? [];
  const hasMode = !!status?.capabilities.permission_mode || !!h.defaults.permission_mode;
  const mark = changed ? " is-changed" : "";
  return (
    <>
      <div className="hn-field">
        <span className="hn-field-label">enabled</span>
        <div className="hn-switch-row">
          <Switch checked={h.enabled} onChange={(enabled) => patch({ enabled })} label={`${h.enabled ? "Disable" : "Enable"} ${h.id}`} />
          <span className={`hn-field-help${mark}`}>{h.enabled ? "enabled" : "disabled: a task selecting it stops for a human"}</span>
        </div>
      </div>
      <ModelField label="executable" value={h.executable ?? ""} placeholder={status?.executable ?? "the provider's own"} changed={changed} clearable onCommit={(v) => patch({ executable: v || null })} />
      <div className="hn-field"><p className="hn-field-help"><span className={`hn-chip${h.executable_found ? "" : " is-bad"}`}>{h.executable_found ? "found" : "not on PATH"}</span> provider {h.provider ?? "unknown"}</p></div>
      <ModelField label="default model" value={h.defaults.model ?? ""} suggestions={status?.models} changed={changed} clearable onCommit={(v) => setDefault("model", v)} />
      {(efforts.length > 0 || h.defaults.effort) && (
        <div className="hn-field">
          <span className="hn-field-label">default effort</span>
          <Segmented label="Default effort" options={[{ value: "", label: "not set" }, ...efforts.map((e) => ({ value: e, label: e }))]} value={h.defaults.effort ?? ""} onChange={(v) => setDefault("effort", v)} />
        </div>
      )}
      {hasMode && (modes.length > 0 ? (
        <div className="hn-field">
          <span className="hn-field-label">permission mode</span>
          <Segmented label="Permission mode" options={[{ value: "", label: "not set" }, ...modes.map((m) => ({ value: m, label: m }))]} value={h.defaults.permission_mode ?? ""} onChange={(v) => setDefault("permission_mode", v)} />
        </div>
      ) : (
        <ModelField label="permission mode" value={h.defaults.permission_mode ?? ""} changed={changed} clearable onCommit={(v) => setDefault("permission_mode", v)} />
      ))}
      {error && <p className="hn-error" role="alert">{error}</p>}
      {onYaml && <Note><button type="button" className="hn-link" onClick={() => onYaml("harnesses.yaml")}>Edit in YAML</button> for the rest of the file.</Note>}
    </>
  );
}

/** The pane of one harness (Decisions §11 Harness selected): its fields, Access, what the provider accepts. */
export function HarnessPane({ h, lane, onLane, onProfile, onYaml, ...c }: Common & {
  h: HarnessView;
  lane: string | null;
  onLane: (l: string | null) => void;
  onProfile: (name: string) => void;
  onYaml?: (file: string) => void;
}) {
  const { run, error } = useRun(c.draft);
  const status = c.providers.find((p) => p.id === h.provider);
  const own = c.problems.filter((p) => c.r.harnesses.find((x) => x.id === h.id)?.tasks.some((t) => p.chain === t.chain && p.path === t.path));
  const prob = own[0];

  if (lane) {
    const entry = c.r.profiles[lane]?.providers[h.provider ?? ""];
    return (
      <Inspector id="harnesses-lane" open={c.open} size={c.size} crumbs={[{ label: "Harnesses", onClick: () => onLane(null) }, { label: h.id, onClick: () => onLane(null) }]} icon="layers" title={lane} sub={`profile on ${h.id}`} onCollapse={c.onCollapse} onExpand={c.onExpand}>
        <Head>{lane} on {h.id}</Head>
        {entry ? (
          <>
            <EntryEditor draft={c.draft} profile={lane} provider={h.provider ?? ""} entry={entry} status={status} changed={c.changes.has(`profiles.${lane}`)} />
            <Note>Shared by every {h.provider} harness. Editing it here edits the profile.</Note>
          </>
        ) : (
          <>
            <Note bad>{lane} has no {h.provider} entry.</Note>
            <AddEntry draft={c.draft} profile={lane} provider={h.provider ?? ""} status={status} />
          </>
        )}
        <Kv k="profile" v={`Open profile ${lane} →`} onClick={() => onProfile(lane)} />
      </Inspector>
    );
  }

  return (
    <Inspector
      id="harnesses-harness"
      open={c.open}
      size={c.size}
      crumbs={[{ label: "Harnesses", onClick: () => onLane(null) }]}
      icon="bot"
      title={h.id}
      sub={`harness · ${h.provider} · ${h.tasks.filter((t) => !t.fallback).length} tasks`}
      prob={prob ? { msg: problemText(prob), fix: prob.fix } : undefined}
      onCollapse={c.onCollapse}
      onExpand={c.onExpand}
    >
      <Head>Harness</Head>
      <HarnessFields draft={c.draft} h={h} status={status} changed={c.changes.has(`harnesses.${h.id}`)} onYaml={onYaml} />

      <Head>Access</Head>
      <div className="hn-field">
        <span className="hn-field-label">tasks may</span>
        <Segmented label="Access" options={ACCESS} value={h.state} onChange={(state) => void run({ op: "set_access", harness: h.id, state })} />
        <p className={`hn-field-help${c.changes.has(`access.${h.id}`) ? " is-changed" : ""}`}>{HELP[h.state]}</p>
      </div>
      <Note>Written to policy.yaml as allowed_harnesses: Available is in defaults and maxima, Override in maxima only, Never in neither. Each lane on the canvas shows this as a tag.</Note>
      {c.r.escalation_effective.harness === h.id && <Note>Escalation turns run on this harness (area pane → Escalation).</Note>}
      {error && <p className="hn-error" role="alert">{error}</p>}

      <Head>Provider accepts</Head>
      <Note>effort: {status?.efforts.length ? status.efforts.join(", ") : "none"}</Note>
      <Note>models: {status?.models.length ? status.models.join(", ") : "any"}</Note>
      {status && Object.entries(status.capabilities).map(([name, cap]) => (
        <Kv key={name} k={name} v={cap.cli.join(" ") || "read from the provider"} mono muted />
      ))}
    </Inspector>
  );
}
