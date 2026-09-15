import { useEffect, useState } from "react";
import { CaretRight, Check, Plus, WarningCircle } from "@phosphor-icons/react";
import { useSearchParams } from "react-router-dom";
import * as api from "../../api";
import { OverflowMenu, Row, RowText, SectionLabel, Switch } from "../../components/ui";
import { backdropProps, useModal } from "../../useModal";
import type { Repo, RepoProbe, TemplateSummary } from "../../types";
import "./repos.css";
import { PageHead, PhoneHeader, usePhone, useResource } from "./shared";

/* ── 5a repos ─────────────────────────────────────────────────────────────── */

/** Parent-then-children ordering. Plain path sort does exactly this: a child's
 *  path is its parent's path plus a separator, so it always sorts immediately
 *  after it and before any sibling of the parent. Sorting, not grouping — no
 *  second mental model, no new component. */
export const sortRepos = (repos: Repo[]): Repo[] =>
  [...repos].sort((a, b) => {
    const as = a.path.split("/");
    const bs = b.path.split("/");
    for (let i = 0; i < Math.min(as.length, bs.length); i++) {
      if (as[i] !== bs[i]) return as[i].localeCompare(bs[i]);
    }
    // One path is a prefix of the other: the shorter (the parent) sorts first.
    return as.length - bs.length;
  });

/** The connected repo this one sits inside, or undefined when it is a root.
 *  Longest match wins, so a grandchild names its immediate parent rather than
 *  the outermost workspace. */
const parentOf = (r: Repo, all: Repo[]): Repo | undefined =>
  all
    .filter((p) => p.path !== r.path && r.path.startsWith(`${p.path}/`))
    .sort((a, b) => b.path.length - a.path.length)[0];

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
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ref = useModal<HTMLFormElement>(onClose);
  // Kraft-avvz: a typed path makes the backdrop click a no-op.
  const dirty = Boolean(path.trim());

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
      await api.addRepo({ path, default_chain_template: tpl });
      if (probe) onAdded(probe);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="Add repo" {...backdropProps(onClose, dirty)}>
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

        {error && <p className="form-error">{error}</p>}
        <div className="dialog-actions">
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || !probe}>
            Connect
          </button>
        </div>
        {/* W7.8: a whole sentence, not a fragment ending on "Repos → …". */}
        <p className="field-hint">
          Connecting writes one entry to repos.yaml. You can change all of it later in Repos
          {probe?.name ? ` → ${probe.name}` : ""}.
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
      const payload = {
        ...draft,
        test_scopes: draft.test_scopes && draft.test_scopes.length > 0 ? draft.test_scopes : null,
      };
      await api.patchRepo(path, payload);
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

      <div className="field">
        <label>
          test scopes <span className="field-hint">· verify runs every matching scope's command</span>
        </label>
        {(current.test_scopes ?? []).length > 0 && (
          <div className="submodule-table">
            {(current.test_scopes ?? []).map((scope, i) => (
              <div key={i} className="scope-row">
                <input
                  className="input mono"
                  placeholder="paths (comma-separated globs)"
                  value={scope.paths.join(", ")}
                  onChange={(e) => {
                    const paths = e.target.value
                      .split(",")
                      .map((p) => p.trim())
                      .filter(Boolean);
                    set({
                      test_scopes: (current.test_scopes ?? []).map((s, j) =>
                        j === i ? { ...s, paths } : s,
                      ),
                    });
                  }}
                />
                <input
                  className="input mono"
                  placeholder="command"
                  value={scope.command}
                  onChange={(e) =>
                    set({
                      test_scopes: (current.test_scopes ?? []).map((s, j) =>
                        j === i ? { ...s, command: e.target.value } : s,
                      ),
                    })
                  }
                />
                <button
                  type="button"
                  className="btn btn-ghost"
                  aria-label={`remove scope ${i + 1}`}
                  onClick={() =>
                    set({ test_scopes: (current.test_scopes ?? []).filter((_, j) => j !== i) })
                  }
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="save-row">
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() =>
              set({ test_scopes: [...(current.test_scopes ?? []), { paths: [], command: "" }] })
            }
          >
            + add scope
          </button>
          {lastProbe?.path === path && lastProbe.probe.test_scopes && (
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => set({ test_scopes: lastProbe.probe.test_scopes })}
            >
              apply {lastProbe.probe.test_scopes.length} probed scope
              {lastProbe.probe.test_scopes.length === 1 ? "" : "s"}
            </button>
          )}
        </div>
        <p className="field-hint">
          none set → every diff runs the test command above. Empty here on Save clears scopes the
          same way.
        </p>
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

      <SectionLabel>Cross-repo</SectionLabel>
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
  const [detectedOpen, setDetectedOpen] = useState(false);
  const phone = usePhone();
  const repos = value?.repos ?? [];
  const selected = params.get("repo");
  const [filter, setFilter] = useState("");
  const q = filter.trim().toLowerCase();
  const matches = (r: Repo) =>
    !q || r.name.toLowerCase().includes(q) || r.path.toLowerCase().includes(q);
  // `managed` is the split, not `enabled`: a repo a human disabled is a
  // decision and stays in the main list reading as deliberately off. Only
  // auto-detected-and-never-touched rows are noise.
  const visible = sortRepos(repos.filter((r) => r.managed && matches(r)));
  const detected = sortRepos(repos.filter((r) => !r.managed && matches(r)));

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
          {repos.length > 0 && (
            <input
              type="search"
              className="input"
              aria-label="filter repos"
              placeholder="Filter repos…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          )}
          {/* m12: a phone row is name, one sub-line and a chevron; the path,
              switch and menu live on the repo page */}
          {phone &&
            visible.map((r: Repo) => (
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
                    !r.enabled && "disabled",
                    parentOf(r, repos) && `in ${parentOf(r, repos)!.name}`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                />
                <CaretRight size={14} />
              </Row>
            ))}
          {!phone && repos.length > 0 && (
            <Row columns="1fr 110px 160px 160px 110px auto" className="repo-row-head">
              <span>Repo</span>
              <span>Template</span>
              <span>Remote</span>
              <span>Test command</span>
              <span>State</span>
              <span />
            </Row>
          )}
          {!phone && visible.map((r: Repo) => (
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
                    {parentOf(r, repos) && <span className="tag"> in {parentOf(r, repos)!.name}</span>}
                  </>
                }
              />
              <span className="row-sub">{r.default_chain_template}</span>
              <span className="row-sub">{r.forge ? `${r.forge} · ${r.project ?? ""}` : "—"}</span>
              <span className="row-sub mono">{r.test_command ?? "not detected"}</span>
              <span className="repo-row-state" onClick={(e) => e.stopPropagation()}>
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
                <span className="row-sub">{r.enabled ? "enabled" : "disabled"}</span>
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
          {detected.length > 0 && (
            <div className="detected-repos">
              <button
                type="button"
                className="detected-summary"
                onClick={() => setDetectedOpen((o) => !o)}
              >
                Detected · {detected.length} · not managed
              </button>
              {detectedOpen && detected.map((r: Repo) => (
                <Row
                  key={r.path}
                  columns="1fr 110px 160px 160px 110px auto"
                  data-repo={r.path}
                  role="button"
                  tabIndex={0}
                  onClick={() => setParams({ repo: r.path })}
                >
                  <RowText title={r.name} sub={<code>{r.path}</code>} />
                  <span className="row-sub">{r.default_chain_template}</span>
                  <span className="row-sub">
                    {r.forge ? `${r.forge} · ${r.project ?? ""}` : "—"}
                  </span>
                  <span className="row-sub mono">{r.test_command ?? "not detected"}</span>
                  <span className="repo-row-state" onClick={(e) => e.stopPropagation()}>
                    <Switch
                      checked={r.enabled}
                      onChange={(next) =>
                        api
                          .patchRepo(r.path, { enabled: next })
                          .then(reload)
                          .catch((e) => setError(e instanceof Error ? e.message : String(e)))
                      }
                      label={`enable ${r.name}`}
                    />
                  </span>
                  <span />
                </Row>
              ))}
            </div>
          )}
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
