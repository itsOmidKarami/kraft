import { useEffect, useState } from "react";
import { CaretRight, Check, Plus, WarningCircle } from "@phosphor-icons/react";
import { useSearchParams } from "react-router-dom";
import * as api from "../../api";
import { OverflowMenu, Row, RowText, SectionLabel, Switch } from "../../components/ui";
import { backdropProps, useModal } from "../../useModal";
import type { Repo, RepoProbe, RepoSubmodule, TemplateSummary } from "../../types";
import "./repos.css";
import { PageHead, PhoneHeader, usePhone, useResource } from "./shared";

/* ── 5a repos ─────────────────────────────────────────────────────────────── */

function AddRepo({
  templates,
  onClose,
  onAdded,
}: {
  templates: TemplateSummary[];
  onClose: () => void;
  onAdded: (probe: RepoProbe) => void;
}) {
  const [path, setPath] = useState("");
  const [probe, setProbe] = useState<RepoProbe | null>(null);
  const [tpl, setTpl] = useState("default");
  const [crossRepo, setCrossRepo] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ref = useModal<HTMLFormElement>(onClose);

  // Probing is read-only, so it can run as the path is typed — the dialog shows
  // what Kraft found before anything is written.
  useEffect(() => {
    if (!path.trim()) {
      setProbe(null);
      setError(null);
      return;
    }
    const t = setTimeout(() => {
      api
        .probeRepo(path)
        .then((p) => {
          setProbe(p);
          setError(null);
        })
        .catch((e) => {
          setProbe(null);
          setError(e instanceof Error ? e.message : String(e));
        });
    }, 300);
    return () => clearTimeout(t);
  }, [path]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.addRepo({ path, default_chain_template: tpl, allow_cross_repo: crossRepo });
      if (probe) onAdded(probe);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="Add repo" {...backdropProps(onClose)}>
      <form className="dialog intake" onSubmit={submit} ref={ref}>
        <div className="dialog-title">Add repo</div>
        <div className="field">
          <label htmlFor="repo-path">Path</label>
          <input
            id="repo-path"
            className="input mono"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            required
          />
        </div>

        {probe && (
          <div className="probe">
            <span>
              <Check size={13} className="probe-ok" />
              git repo · {probe.branch}
              {probe.submodules.length
                ? ` · ${probe.submodules.length} submodule${probe.submodules.length === 1 ? "" : "s"} (${probe.submodules.join(", ")})`
                : " · no submodules"}
            </span>
            <span>
              {probe.beads_export_auto ? (
                <Check size={13} className="probe-ok" />
              ) : (
                <WarningCircle size={13} className="probe-warn" />
              )}
              {probe.has_beads ? ".beads/ present" : "no .beads/ yet"} ·{" "}
              {probe.beads_export_auto ? "export.auto on" : "export.auto off — not in the hub"}
            </span>
            <span>
              {probe.has_engineering ? (
                <Check size={13} className="probe-ok" />
              ) : (
                <WarningCircle size={13} className="probe-warn" />
              )}
              {probe.has_engineering
                ? ".engineering/ present"
                : "no .engineering/ yet — created on first spec"}
            </span>
          </div>
        )}

        <div className="field">
          <label>Default chain template</label>
          <div className="seg" role="radiogroup" aria-label="default template">
            {(templates.length ? templates.map((t) => t.id) : ["default"]).map((id) => (
              <label key={id} className="seg-opt">
                <input
                  type="radio"
                  name="repo-template"
                  checked={tpl === id}
                  onChange={() => setTpl(id)}
                />
                {id}
              </label>
            ))}
          </div>
        </div>

        <div className="field">
          <label>
            Test command <span className="field-hint">· on.test.run</span>
          </label>
          <div className="input readout mono">{probe?.test_command ?? "not detected"}</div>
        </div>
        <div className="field">
          <label>
            Forge project <span className="field-hint">· on.mr.open / on.ci.poll / on.merge</span>
          </label>
          <div className="input readout">
            {probe?.forge
              ? probe.project
                ? `${probe.forge} · ${probe.project}`
                : probe.forge
              : "no forge remote detected"}
          </div>
        </div>

        <div className="field">
          <Switch checked={crossRepo} onChange={setCrossRepo} label="allow cross-repo items" />
          <span className="field-hint">
            {probe && probe.submodules.length === 0
              ? "off — no submodules to cross"
              : "allow cross-repo items"}
          </span>
        </div>

        {error && <p className="form-error">{error}</p>}
        <div className="dialog-actions">
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || !probe}>
            Connect
          </button>
        </div>
        <p className="field-hint">
          writes one entry to repos.yaml · you can change everything later in Repos →{" "}
          {probe?.name ?? "…"}
        </p>
      </form>
    </div>
  );
}

/* ── 5b repo detail (desktop 25, phone m12 detail) ──────────────────────── */

const ROOT_MERGE_LABEL: Record<Repo["default_root_merge_policy"], string> = {
  bump: "Bump",
  skip: "Skip",
  bump_no_mr: "Bump, no MR",
};

function RepoDetail({
  path,
  repos,
  templates,
  lastProbe,
  onReprobe,
  onClose,
  reload,
}: {
  path: string;
  repos: Repo[];
  templates: TemplateSummary[];
  lastProbe: { path: string; probe: RepoProbe } | null;
  onReprobe: () => void;
  onClose: () => void;
  reload: () => Promise<void>;
}) {
  const phone = usePhone();
  const repo = repos.find((r) => r.path === path);
  const [draft, setDraft] = useState<Repo | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const current = draft ?? repo;
  const dirty = draft !== null;

  if (!repo || !current) return null;

  const set = (patch: Partial<Repo>) => setDraft({ ...current, ...patch });

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.patchRepo(path, draft);
      setDraft(null);
      await reload();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setBusy(true);
    try {
      await api.deleteRepo(path);
      onClose();
      await reload();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const probedSubmodulePaths =
    lastProbe?.path === path ? lastProbe.probe.submodules : [];
  const savedPaths = new Set(current.submodules.map((s) => s.path));
  const submoduleRows: RepoSubmodule[] = [
    ...current.submodules,
    ...probedSubmodulePaths
      .filter((p) => !savedPaths.has(p))
      .map((p) => ({ path: p, enabled: false, test_command: null, chain_override: null })),
  ];

  const setSubmodule = (subPath: string, patch: Partial<RepoSubmodule>) => {
    const existing = submoduleRows.find((s) => s.path === subPath);
    const next = { ...(existing ?? { path: subPath, enabled: false, test_command: null, chain_override: null }), ...patch };
    const rest = current.submodules.filter((s) => s.path !== subPath);
    set({ submodules: [...rest, next] });
  };

  const body = (
    <>
      <SectionLabel>General</SectionLabel>
      <div className="field">
        <Switch
          checked={current.enabled}
          onChange={(next) => set({ enabled: next })}
          label={`${current.enabled ? "disable" : "enable"} ${current.name}`}
        />
        <span className="field-hint">
          {current.enabled
            ? "enabled — new items can target it"
            : "disabled — new items can't target it; running items keep going"}
        </span>
      </div>
      <div className="field">
        <label htmlFor="repo-name">name</label>
        <input
          id="repo-name"
          className="input"
          value={current.name}
          onChange={(e) => set({ name: e.target.value })}
        />
      </div>
      <div className="field">
        <label>path</label>
        <div className="input readout mono">{current.path}</div>
      </div>
      <div className="field">
        <label>default chain template</label>
        <div className="seg" role="radiogroup" aria-label="default template">
          {(templates.length ? templates.map((t) => t.id) : [current.default_chain_template]).map(
            (id) => (
              <label key={id} className="seg-opt">
                <input
                  type="radio"
                  name="repo-default-template"
                  checked={current.default_chain_template === id}
                  onChange={() => set({ default_chain_template: id })}
                />
                {id}
              </label>
            ),
          )}
        </div>
      </div>
      <div className="field">
        <label htmlFor="repo-default-model">default model</label>
        <input
          id="repo-default-model"
          className="input"
          placeholder="inherit from registry"
          value={current.default_model ?? ""}
          onChange={(e) => set({ default_model: e.target.value || null })}
        />
      </div>

      <SectionLabel>Testing</SectionLabel>
      <div className="field">
        <label htmlFor="repo-test-command">test command · on.test.run</label>
        <input
          id="repo-test-command"
          className="input mono"
          placeholder="verify fails until set · e.g. uv run pytest -q"
          value={current.test_command ?? ""}
          onChange={(e) => set({ test_command: e.target.value || null })}
        />
      </div>

      <SectionLabel>Forge</SectionLabel>
      <p className="settings-note">on.mr.open · on.ci.poll · on.mr.sync · on.merge</p>
      <div className="field">
        <div className="seg" role="radiogroup" aria-label="forge">
          {(["gitlab", "github", "none"] as const).map((f) => (
            <label key={f} className="seg-opt">
              <input
                type="radio"
                name="repo-forge"
                checked={(current.forge ?? "none") === f}
                onChange={() => set({ forge: f === "none" ? null : f })}
              />
              {f}
            </label>
          ))}
        </div>
      </div>
      <div className="field">
        <label htmlFor="repo-project">project</label>
        <input
          id="repo-project"
          className="input"
          placeholder="from the origin remote"
          value={current.project ?? ""}
          onChange={(e) => set({ project: e.target.value || null })}
        />
      </div>

      <SectionLabel>Submodules</SectionLabel>
      <div className="field">
        <Switch
          checked={current.allow_cross_repo}
          onChange={(next) => set({ allow_cross_repo: next })}
          label="allow cross-repo items"
        />
      </div>
      <div className="field">
        <label>root merge policy</label>
        <div className="seg" role="radiogroup" aria-label="default root merge policy">
          {(["bump", "skip", "bump_no_mr"] as const).map((p) => (
            <label key={p} className="seg-opt">
              <input
                type="radio"
                name="repo-root-merge"
                checked={current.default_root_merge_policy === p}
                onChange={() => set({ default_root_merge_policy: p })}
              />
              {ROOT_MERGE_LABEL[p]}
            </label>
          ))}
        </div>
      </div>
      {submoduleRows.length > 0 && (
        <div className="submodule-table">
          <div className="submodule-row submodule-head">
            <span>ON</span>
            <span>SUBMODULE</span>
            <span>TEST COMMAND</span>
            <span>CHAIN OVERRIDE</span>
          </div>
          {submoduleRows.map((s) => (
            <div key={s.path} className="submodule-row">
              <Switch
                checked={s.enabled}
                onChange={(next) => setSubmodule(s.path, { enabled: next })}
                label={s.path}
              />
              <span className="mono">{s.path}</span>
              <input
                className="input mono"
                value={s.test_command ?? ""}
                onChange={(e) => setSubmodule(s.path, { test_command: e.target.value || null })}
              />
              <select
                className="input"
                value={s.chain_override ?? ""}
                onChange={(e) => setSubmodule(s.path, { chain_override: e.target.value || null })}
              >
                <option value="">inherit</option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.id}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>
      )}
      <p className="settings-note">
        Off = the item can't declare it and on.repos.scan flags any edit to it. A submodule that
        is itself a connected repo (libs/repo-a → repo-a) takes that repo's settings unless
        overridden here.
      </p>

      <SectionLabel>Agent</SectionLabel>
      <div className="field">
        <label>steering</label>
        <div className="submodules">
          {current.steering.map((name) => (
            <button
              key={name}
              type="button"
              className="tag"
              onClick={() => set({ steering: current.steering.filter((n) => n !== name) })}
            >
              {name} ✕
            </button>
          ))}
          <button
            type="button"
            className="tag tag-off"
            onClick={() => {
              const name = window.prompt("Steering file name");
              if (name) set({ steering: [...current.steering, name] });
            }}
          >
            + add
          </button>
        </div>
      </div>
      <div className="field">
        <label>deny tools</label>
        <div className="submodules">
          {current.deny_tools.map((name) => (
            <button
              key={name}
              type="button"
              className="tag"
              onClick={() => set({ deny_tools: current.deny_tools.filter((n) => n !== name) })}
            >
              {name} ✕
            </button>
          ))}
          <button
            type="button"
            className="tag tag-off"
            onClick={() => {
              const name = window.prompt("Tool name to deny");
              if (name) set({ deny_tools: [...current.deny_tools, name] });
            }}
          >
            + add
          </button>
        </div>
      </div>

      <div className="save-row">
        <button className="btn btn-primary" disabled={busy || !dirty} onClick={save}>
          <Check size={14} />
          Save
        </button>
        <button className="btn btn-ghost" disabled={busy || !dirty} onClick={() => setDraft(null)}>
          Revert
        </button>
        <span className="save-hint">{message ?? "writes repos.yaml"}</span>
        <button
          type="button"
          className="btn btn-ghost btn-danger"
          disabled={busy}
          onClick={disconnect}
        >
          {confirming ? `Disconnect ${current.name}? click again` : `Disconnect ${current.name}…`}
        </button>
      </div>
    </>
  );

  if (phone) {
    return (
      <div className="repo-detail">
        <PhoneHeader
          back="Repos"
          backTo="/settings/repos"
          title={current.name}
          subtitle={`${current.path} · ${current.default_chain_template}`}
          action={
            <button className="btn btn-primary" disabled={busy || !dirty} onClick={save}>
              Save
            </button>
          }
        />
        {body}
      </div>
    );
  }

  return (
    <div className="repo-detail">
      <div className="settings-head">
        <h2>{current.name}</h2>
        <span className="settings-note">
          <code>{current.path}</code>
        </span>
        <button className="btn btn-secondary" onClick={onReprobe}>
          ↻ Re-probe
        </button>
      </div>
      {body}
    </div>
  );
}

/* ── 5a repos list (desktop 24, phone m12 list) ──────────────────────────── */

export function ReposPage() {
  const { value, error, setError, reload } = useResource(() => api.getRepos());
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [adding, setAdding] = useState(false);
  const [lastProbe, setLastProbe] = useState<{ path: string; probe: RepoProbe } | null>(null);
  const [params, setParams] = useSearchParams();
  const phone = usePhone();
  const repos = value?.repos ?? [];
  const selected = params.get("repo");

  useEffect(() => {
    api.getTemplates().then(setTemplates).catch(() => {});
  }, []);

  const reprobe = () => {
    if (!selected) return;
    api.probeRepo(selected).then((probe) => setLastProbe({ path: selected, probe }));
  };

  const showList = !phone || !selected;
  const showDetail = selected != null;

  return (
    <>
      {showList && (
        <>
          {phone ? (
            <PhoneHeader
              back="Settings"
              backTo="/settings"
              title="Repos"
              subtitle={`${repos.length} connected`}
              action={
                <button className="btn btn-primary" aria-label="Add repo" onClick={() => setAdding(true)}>
                  <Plus size={14} />
                </button>
              }
            />
          ) : (
            <PageHead
              title="Repos"
              note={`${repos.length} connected · the hydration hub aggregates all of them`}
              action={
                <button className="btn btn-primary settings-action" onClick={() => setAdding(true)}>
                  <Plus size={14} />
                  Add repo
                </button>
              }
            />
          )}
          {error && <p className="form-error">{error}</p>}
          {repos.length === 0 && !error && <p className="empty">no repos connected yet</p>}
          {/* m12: a phone row is name, one sub-line and a chevron; the path,
              switch and menu live on the repo page */}
          {phone &&
            repos.map((r: Repo) => (
              <Row
                key={r.path}
                columns="minmax(0, 1fr) auto"
                data-repo={r.path}
                role="button"
                tabIndex={0}
                onClick={() => setParams({ repo: r.path })}
              >
                <RowText
                  title={r.name}
                  sub={[
                    r.default_chain_template,
                    r.forge && `${r.forge} ${r.project ?? ""}`.trim(),
                    r.submodules.length > 0 && `${r.submodules.length} submodules`,
                    !r.enabled && "disabled",
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                />
                <CaretRight size={14} />
              </Row>
            ))}
          {!phone && repos.map((r: Repo) => (
            <Row
              key={r.path}
              columns="1fr 110px 160px 160px 110px auto"
              data-repo={r.path}
              data-selected={r.path === selected || undefined}
              role="button"
              tabIndex={0}
              onClick={() => setParams({ repo: r.path })}
            >
              <RowText
                title={r.name}
                sub={
                  <>
                    <code>{r.path}</code>
                    {r.submodules.length > 0 && ` · ${r.submodules.length} submodules`}
                  </>
                }
              />
              <span className="row-sub">{r.default_chain_template}</span>
              <span className="row-sub">{r.forge ? `${r.forge} · ${r.project ?? ""}` : "—"}</span>
              <span className="row-sub mono">{r.test_command ?? "not detected"}</span>
              <span onClick={(e) => e.stopPropagation()}>
                <Switch
                  checked={r.enabled}
                  onChange={(next) =>
                    api
                      .patchRepo(r.path, { enabled: next })
                      .then(reload)
                      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
                  }
                  label={`${r.enabled ? "disable" : "enable"} ${r.name}`}
                />
              </span>
              <span onClick={(e) => e.stopPropagation()}>
                <OverflowMenu
                  items={[
                    {
                      label: "Disconnect",
                      danger: true,
                      confirm: `Disconnect ${r.name}? Work items already running against it keep going.`,
                      onSelect: () =>
                        api
                          .deleteRepo(r.path)
                          .then(reload)
                          .catch((e) => setError(e instanceof Error ? e.message : String(e))),
                    },
                  ]}
                />
              </span>
            </Row>
          ))}
          {lastProbe && (
            <p className="settings-note">
              {lastProbe.path} · probe, just now · {lastProbe.probe.submodules.length} submodules
            </p>
          )}
          <p className="settings-foot">
            A repo needs <code>export.auto</code> and <code>export.git-add</code> on in its beads
            config to appear in the hub; Kraft flags it but does not change repo files.
          </p>
        </>
      )}
      {showDetail && (
        <RepoDetail
          // Remounts (resetting `draft`) whenever the selected repo changes —
          // otherwise an unsaved edit to repo A survives the click to repo B
          // and Save would patch B with A's values.
          key={selected}
          path={selected}
          repos={repos}
          templates={templates}
          lastProbe={lastProbe}
          onReprobe={reprobe}
          onClose={() => setParams({})}
          reload={reload}
        />
      )}
      {adding && (
        <AddRepo
          templates={templates}
          onClose={() => setAdding(false)}
          onAdded={(probe) => {
            setLastProbe({ path: probe.path, probe });
            reload();
          }}
        />
      )}
    </>
  );
}
