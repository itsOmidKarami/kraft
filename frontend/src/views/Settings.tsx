import { useCallback, useEffect, useMemo, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { Check, Plus, WarningCircle } from "@phosphor-icons/react";
import * as api from "../api";
import { ago, until } from "../format";
import { DraftDiff } from "../components/DraftDiff";
import { ChainBar, OverflowMenu, Row, RowText, SectionLabel } from "../components/ui";
import { backdropProps, useModal } from "../useModal";
import { PALETTES, applyTheme } from "../theme";
import type {
  Access,
  AuthSession,
  HookBinding,
  Intake,
  Notify,
  Policy,
  Repo,
  RepoProbe,
  SteeringList,
  TemplateSummary,
  TemplateValidation,
  Theme,
} from "../types";

/**
 * Settings (design 5a–5e). Every page edits versioned YAML through the API
 * (`02` §4.7 revised), so each one ends in the same save row: what file is
 * written, and what re-runs when it lands.
 */

const PAGES = [
  { to: "repos", label: "Repos" },
  { to: "templates", label: "Chain templates" },
  { to: "plugins", label: "Plugins" },
  { to: "steering", label: "Steering" },
  { to: "policy", label: "Policy" },
  { to: "appearance", label: "Appearance" },
  { to: "intake", label: "Auto-intake" },
  { to: "notify", label: "Notifications" },
  { to: "access", label: "Access" },
];

/** Load-once-then-edit, the shape every page here needs. */
function useResource<T>(load: () => Promise<T>) {
  const [value, setValue] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    return load()
      .then((v) => {
        setValue(v);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    // `load` is redefined every render by design — the caller closes over its own
    // state — so it is deliberately not a dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // `reload` now returns a promise (so callers can await it); wrap it here
  // because `useEffect` requires `void | Destructor` — passing the promise
  // straight through would make React treat it as a cleanup function. Do
  // not "simplify" this back to `useEffect(reload, [reload])`.
  useEffect(() => {
    void reload();
  }, [reload]);
  return { value, setValue, error, setError, reload };
}

function SaveRow({
  onSave,
  onDiscard,
  hint,
  busy,
  dirty,
  message,
}: {
  onSave: () => void;
  onDiscard: () => void;
  hint: string;
  busy?: boolean;
  dirty?: boolean;
  message?: string | null;
}) {
  return (
    <div className="save-row">
      <button className="btn btn-primary" disabled={busy || !dirty} onClick={onSave}>
        <Check size={14} />
        Save
      </button>
      <button className="btn btn-ghost" disabled={busy || !dirty} onClick={onDiscard}>
        Discard
      </button>
      <span className="save-hint">{message ?? hint}</span>
    </div>
  );
}

function PageHead({ title, note, action }: { title: string; note: string; action?: React.ReactNode }) {
  return (
    <div className="settings-head">
      <h2>{title}</h2>
      <span className="settings-note">{note}</span>
      {action}
    </div>
  );
}

/* ── 5a repos ─────────────────────────────────────────────────────────────── */

function AddRepo({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [path, setPath] = useState("");
  const [probe, setProbe] = useState<RepoProbe | null>(null);
  const [tpl, setTpl] = useState("default");
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ref = useModal<HTMLFormElement>(onClose);

  useEffect(() => {
    api.getTemplates().then(setTemplates).catch(() => {});
  }, []);

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
      onAdded();
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

        {error && <p className="form-error">{error}</p>}
        <div className="dialog-actions">
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || !probe}>
            Connect
          </button>
        </div>
      </form>
    </div>
  );
}

function ReposPage() {
  const { value, error, setError, reload } = useResource(() => api.getRepos());
  const [adding, setAdding] = useState(false);
  const repos = value?.repos ?? [];

  return (
    <>
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
      {error && <p className="form-error">{error}</p>}
      {repos.length === 0 && !error && <p className="empty">no repos connected yet</p>}
      {repos.map((r: Repo) => (
        <Row key={r.path} columns="1fr 110px 110px auto" data-repo={r.path}>
          <RowText title={r.name} sub={<code>{r.path}</code>} />
          <span className="row-sub">{r.default_chain_template}</span>
          <span className="row-sub">{r.enabled ? "enabled" : "disabled"}</span>
          <OverflowMenu
            items={[
              {
                label: r.enabled ? "Disable" : "Enable",
                onSelect: () =>
                  api
                    .patchRepo(r.path, { enabled: !r.enabled })
                    .then(reload)
                    .catch((e) => setError(e instanceof Error ? e.message : String(e))),
              },
              {
                label: "Disconnect",
                danger: true,
                // Nothing undoes this from here — you re-probe and re-add the
                // repo. Ask before, not after (NN/g: confirm what cannot be undone).
                confirm: `Disconnect ${r.name}? Work items already running against it keep going.`,
                onSelect: () =>
                  api
                    .deleteRepo(r.path)
                    .then(reload)
                    .catch((e) => setError(e instanceof Error ? e.message : String(e))),
              },
            ]}
          />
        </Row>
      ))}
      <p className="settings-foot">
        A repo needs <code>export.auto</code> and <code>export.git-add</code> on in its beads
        config to appear in the hub; Kraft flags it but does not change repo files.
      </p>
      {adding && <AddRepo onClose={() => setAdding(false)} onAdded={reload} />}
    </>
  );
}

/* ── 5b chain templates ───────────────────────────────────────────────────── */

function TemplatesPage() {
  const { value, error, reload } = useResource(() => api.getTemplates());
  const templates = useMemo(() => value ?? [], [value]);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [report, setReport] = useState<TemplateValidation | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showDiff, setShowDiff] = useState(false);

  const current = templates.find((t) => t.id === selected) ?? templates[0];
  useEffect(() => {
    if (current && current.id !== selected) setSelected(current.id);
  }, [current, selected]);

  const original = useMemo(
    () => (current ? JSON.stringify(current.nodes, null, 2) : ""),
    [current],
  );
  useEffect(() => setDraft(original), [original]);

  const parsed = useMemo(() => {
    try {
      const nodes = JSON.parse(draft || "[]");
      return Array.isArray(nodes) ? nodes : null;
    } catch {
      return null;
    }
  }, [draft]);

  const check = async () => {
    if (!current || !parsed) return;
    setReport(await api.validateTemplate(current.id, parsed));
  };

  const save = async () => {
    if (!current || !parsed) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.putTemplate(current.id, parsed);
      await reload();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHead
        title="Chain templates"
        note="a live work item keeps the chain it materialized at intake; edits affect new items"
      />
      {error && <p className="form-error">{error}</p>}
      <div className="template-editor">
        <div className="template-list">
          <SectionLabel>Templates</SectionLabel>
          {templates.map((t) => (
            <button
              key={t.id}
              className="facet-opt"
              aria-pressed={t.id === current?.id}
              onClick={() => setSelected(t.id)}
            >
              {t.id}
              <span className="facet-count">
                {t.nodes.length} nodes · {t.gates} gates
              </span>
            </button>
          ))}
        </div>
        <div className="template-draft">
          <label className="field-hint" htmlFor="template-nodes">
            nodes · validated against the registry before it is written
          </label>
          <textarea
            id="template-nodes"
            aria-label="template nodes"
            className="input mono template-yaml"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          {!parsed && <p className="form-error">not valid JSON — nothing will be saved</p>}
          {/* The diagram tracks what is typed: `parsed` is the draft, so it
              updates as the textarea changes and vanishes into the "not valid
              JSON" line below when it cannot be read. No graph library — a
              chain is a list with gates, and ChainBar already draws one. */}
          {parsed && (
            <ChainBar
              nodes={parsed}
              invalid={(report?.unresolved ?? []).map((u) => u.node)}
              size="lg"
            />
          )}
          {showDiff && <DraftDiff before={original} after={draft} />}
          {report && (
            <div className="validation" data-valid={report.valid}>
              <span>{report.valid ? "valid" : report.error}</span>
              {report.unresolved.map((u) => (
                <span key={`${u.node}/${u.task}`} className="row-sub">
                  {u.node}: {u.task} does not resolve
                </span>
              ))}
            </div>
          )}
          <div className="save-row">
            <button className="btn btn-primary" disabled={busy || !parsed} onClick={save}>
              <Check size={14} />
              Save
            </button>
            <button className="btn btn-secondary" disabled={!parsed} onClick={check}>
              Validate
            </button>
            <button className="btn btn-secondary" onClick={() => setShowDiff((v) => !v)}>
              Changes
            </button>
            <button className="btn btn-ghost" onClick={() => setDraft(original)}>
              Discard
            </button>
            <span className="save-hint">
              {message ?? `writes ${current?.id ?? "the template"}.yaml · validator re-runs`}
            </span>
          </div>
        </div>
      </div>
    </>
  );
}

/* ── 5c plugins ───────────────────────────────────────────────────────────── */

const adapterOf = (b: HookBinding) =>
  b.kind === "builtin"
    ? `builtin · ${b.handler}`
    : Array.isArray(b.command)
      ? b.command.join(" ")
      : (b.command ?? b.kind);

function PluginsPage() {
  const { value, error, reload } = useResource(() => api.getRegistry());
  const [draft, setDraft] = useState<Record<string, HookBinding> | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const hooks = draft ?? value?.hooks ?? {};
  const dirty = draft !== null;

  const toggle = (hook: string) =>
    setDraft({
      ...hooks,
      [hook]: { ...hooks[hook], interactive: !hooks[hook].interactive },
    });

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

  return (
    <>
      <PageHead
        title="Plugins"
        note="one binding per hook in v1 · a hook disabled for a repo makes any template using it unresolvable there"
      />
      {error && <p className="form-error">{error}</p>}
      <div className="hook-row hook-head">
        <span>Hook → plugin</span>
        <span>Adapter</span>
        <span>Steer</span>
      </div>
      {Object.entries(hooks).map(([hook, binding]) => (
        <div key={hook} className="hook-row" data-hook={hook}>
          <span className="hook-name">{hook}</span>
          <span className="row-sub">{adapterOf(binding)}</span>
          <button
            className="switch"
            role="switch"
            aria-checked={!!binding.interactive}
            aria-label={`steerable: ${hook}`}
            onClick={() => toggle(hook)}
          >
            <span className="switch-knob" />
          </button>
        </div>
      ))}
      <SaveRow
        onSave={save}
        onDiscard={() => setDraft(null)}
        dirty={dirty}
        busy={busy}
        message={message}
        hint="writes registry.yaml · validator re-runs · affects intake only, live items keep their chain"
      />
      <p className="settings-foot">
        Steerable means the hook runs a headless agent Kraft can stop and relaunch with a note.
        It comes from the registry (<code>interactive: true</code>), not from the client.
      </p>
    </>
  );
}

/* ── 5d-bis auto-intake ───────────────────────────────────────────────────── */

function IntakePage() {
  const { value, error, reload } = useResource(() => api.getIntake());
  const [draft, setDraft] = useState<Intake | null>(null);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const intake = draft ?? value;
  const dirty = draft !== null;

  useEffect(() => {
    api
      .getRepos()
      .then((r) => setRepos(r.repos))
      .catch(() => setRepos([]));
  }, []);

  const set = <K extends keyof Intake>(field: K, v: Intake[K]) => {
    if (!intake) return;
    setDraft({ ...intake, [field]: v });
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.putIntake(draft);
      setDraft(null);
      await reload();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHead
        title="Auto-intake"
        note="polls the beads hub for ready work and starts it unattended — only chains with a gate, and never past the daily budget"
      />
      {error && <p className="form-error">{error}</p>}
      {intake && (
        <>
          <section className="settings-section">
            <h6>Poller</h6>
            <div className="save-row">
              <button
                className="switch"
                role="switch"
                aria-label="Auto-intake"
                aria-checked={intake.enabled}
                disabled={busy}
                onClick={() => set("enabled", !intake.enabled)}
              >
                <span className="switch-knob" />
              </button>
              <span className="save-hint">
                {intake.enabled
                  ? "on — Kraft picks up ready beads on its own"
                  : "off — work starts only when you start it"}
              </span>
            </div>
            <div className="cap-row" data-intake="interval_s">
              <span className="hook-name">Poll every (s)</span>
              <input
                className="input"
                type="number"
                min={30}
                aria-label="poll interval"
                value={intake.interval_s}
                onChange={(e) => set("interval_s", Number(e.target.value))}
              />
              <span className="row-sub">30s floor</span>
            </div>
            <div className="cap-row" data-intake="max_concurrent">
              <span className="hook-name">Max concurrent items</span>
              <input
                className="input"
                type="number"
                min={1}
                aria-label="max concurrent"
                value={intake.max_concurrent}
                onChange={(e) => set("max_concurrent", Number(e.target.value))}
              />
              <span className="row-sub">counts every active item, not only auto-started ones</span>
            </div>
            <div className="cap-row" data-intake="priority_ceiling">
              <span className="hook-name">Priority ceiling</span>
              <input
                className="input"
                type="number"
                min={0}
                max={4}
                aria-label="priority ceiling"
                value={intake.priority_ceiling}
                onChange={(e) => set("priority_ceiling", Number(e.target.value))}
              />
              <span className="row-sub">
                P{intake.priority_ceiling} and below — the highest-priority work is what a human
                should be looking at
              </span>
            </div>
          </section>

          <section className="settings-section">
            <h6>Repos</h6>
            {repos.length === 0 && <p className="empty">no repos configured</p>}
            {repos.map((r) => (
              <label key={r.path} className="radio">
                <input
                  type="checkbox"
                  checked={intake.repos.includes(r.path)}
                  onChange={(e) =>
                    set(
                      "repos",
                      e.target.checked
                        ? [...intake.repos, r.path]
                        : intake.repos.filter((p) => p !== r.path),
                    )
                  }
                />
                <span className="dot" />
                <span>
                  {r.name} <span className="row-sub">· {r.path}</span>
                </span>
              </label>
            ))}
            <p className="settings-foot">
              An empty list means every enabled repo. An epic is never auto-started: it is a
              container for work, not work.
            </p>
          </section>

          <SaveRow
            onSave={save}
            onDiscard={() => setDraft(null)}
            busy={busy}
            dirty={dirty}
            message={message}
            hint="writes intake.yaml · the poller restarts on save, so a change applies without a reboot"
          />
        </>
      )}
    </>
  );
}

/* ── 5c-bis steering ──────────────────────────────────────────────────────── */

function SteeringPage() {
  const { value, error, reload } = useResource(() => api.getSteering());
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [loaded, setLoaded] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const list: SteeringList = value ?? { files: [], max_bytes: 0 };

  // The body is fetched per file rather than shipped with the list: the list is
  // a picker, and every body at once is the injection budget over the wire on
  // every page load.
  useEffect(() => {
    if (selected === null) return;
    api
      .getSteeringFile(selected)
      .then((f) => {
        setDraft(f.body);
        setLoaded(f.body);
      })
      .catch(() => {
        setDraft("");
        setLoaded("");
      });
  }, [selected]);

  const create = () => {
    const name = window.prompt("New steering file (a bare name, no extension)");
    if (!name) return;
    setSelected(name);
    setDraft("");
    setLoaded("");
    setMessage(null);
  };

  const save = async () => {
    if (selected === null) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.putSteeringFile(selected, draft);
      setLoaded(draft);
      await reload();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (selected === null) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.deleteSteeringFile(selected);
      setSelected(null);
      await reload();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // This file's own size, not a total across the directory: MAX_BYTES is the
  // assembled budget of one repo or hook's steering list, so summing unrelated
  // files reads as over budget when nothing is, and under it when something is.
  // Advisory only — the server checks the real assembled total on save.
  const draftBytes = new TextEncoder().encode(draft).length;

  return (
    <>
      <PageHead
        title="Steering"
        note="Kraft-owned standards injected through the system prompt — never CLAUDE.md, never a file inside the target repo"
        action={
          <button className="btn btn-secondary" onClick={create}>
            <Plus size={14} />
            New
          </button>
        }
      />
      {error && <p className="form-error">{error}</p>}
      <div className="template-editor">
        <div className="template-list">
          <SectionLabel>Files</SectionLabel>
          {list.files.map((f) => (
            <button
              key={f.name}
              className="facet-opt"
              aria-pressed={f.name === selected}
              onClick={() => setSelected(f.name)}
            >
              {f.name}
              <span className="facet-count">
                {f.bytes === null ? "unreadable" : `${f.bytes} B`}
              </span>
            </button>
          ))}
          {list.files.length === 0 && <p className="empty">no steering files yet</p>}
        </div>
        <div className="template-draft">
          {selected === null ? (
            <p className="empty">pick a file, or make one</p>
          ) : (
            <>
              <label className="field-hint" htmlFor="steering-body">
                {selected}.md · a hook or repo names this file, and the assembled block is
                re-checked against the budget on save
              </label>
              <textarea
                id="steering-body"
                aria-label="steering body"
                className="input mono template-yaml"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
              />
              {showDiff && <DraftDiff before={loaded} after={draft} />}
              <div className="save-row">
                <button
                  className="btn btn-primary"
                  disabled={busy || draft === loaded}
                  onClick={save}
                >
                  <Check size={14} />
                  Save
                </button>
                <button className="btn btn-secondary" onClick={() => setShowDiff((v) => !v)}>
                  Changes
                </button>
                <button
                  className="btn btn-ghost"
                  disabled={busy || draft === loaded}
                  onClick={() => setDraft(loaded)}
                >
                  Discard
                </button>
                <button className="btn btn-ghost" disabled={busy} onClick={remove}>
                  Delete
                </button>
                <span className="save-hint">
                  {message ??
                    `${draftBytes} B · counts toward the ${list.max_bytes} B assembled ` +
                      `budget of any repo or hook that references this file`}
                </span>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

/* ── 5d policy ────────────────────────────────────────────────────────────── */

function PolicyPage() {
  const { value, error, reload } = useResource(() => api.getPolicy());
  const [draft, setDraft] = useState<Policy | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const policy = draft ?? value;
  const dirty = draft !== null;

  const setCap = (key: string, field: "attempts" | "wall_clock_s", n: number) => {
    if (!policy) return;
    if (key === "default") {
      setDraft({ ...policy, default: { ...policy.default, [field]: n } });
      return;
    }
    setDraft({
      ...policy,
      loops: { ...policy.loops, [key]: { ...policy.loops[key], [field]: n } },
    });
  };

  const setBudget = (field: "work_item_usd" | "daily_usd", raw: string) => {
    if (!policy) return;
    // "" is the operator clearing the cap, which is null — not 0, which would
    // block every launch.
    const n = raw.trim() === "" ? null : Number(raw);
    setDraft({
      ...policy,
      budget: { work_item_usd: null, daily_usd: null, ...policy.budget, [field]: n },
    });
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.putPolicy(draft);
      setDraft(null);
      await reload();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const rows = policy
    ? [...Object.entries(policy.loops), ["default", policy.default] as const]
    : [];

  return (
    <>
      <PageHead
        title="Policy"
        note="every loop stops at attempts or wall-clock, whichever comes first; a spend budget stops the next agent task the same way — the item comes back to you"
      />
      {error && <p className="form-error">{error}</p>}
      <div className="cap-row cap-head">
        <span>Counter</span>
        <span>Attempts</span>
        <span>Wall-clock (s)</span>
      </div>
      {rows.map(([key, cap]) => (
        <div key={key} className="cap-row" data-loop={key}>
          <span className="hook-name">{key}</span>
          <input
            className="input"
            type="number"
            min={1}
            aria-label={`${key} attempts`}
            value={cap.attempts}
            onChange={(e) => setCap(key, "attempts", Number(e.target.value))}
          />
          <input
            className="input"
            type="number"
            min={1}
            aria-label={`${key} wall clock`}
            value={cap.wall_clock_s}
            onChange={(e) => setCap(key, "wall_clock_s", Number(e.target.value))}
          />
        </div>
      ))}
      <SectionLabel>Budget</SectionLabel>
      <div className="cap-row budget-row" data-budget="work_item_usd">
        <span className="hook-name">Per work item ($)</span>
        <input
          className="input"
          type="number"
          min={0}
          step="0.01"
          aria-label="work item budget"
          value={policy?.budget?.work_item_usd ?? ""}
          onChange={(e) => setBudget("work_item_usd", e.target.value)}
        />
      </div>
      <div className="cap-row budget-row" data-budget="daily_usd">
        <span className="hook-name">Per day ($)</span>
        <input
          className="input"
          type="number"
          min={0}
          step="0.01"
          aria-label="daily budget"
          value={policy?.budget?.daily_usd ?? ""}
          onChange={(e) => setBudget("daily_usd", e.target.value)}
        />
      </div>
      <p className="settings-note">
        Blank is no cap. A cap refuses to start the next agent task; it cannot stop
        one already running, because an agent only reports its cost when its session
        ends. Expect to overshoot by up to the cost of one task.
      </p>
      <SaveRow
        onSave={save}
        onDiscard={() => setDraft(null)}
        dirty={dirty}
        busy={busy}
        message={message}
        hint="writes policy.yaml · applies to loops that start after the save"
      />
    </>
  );
}

/* ── 5g appearance ────────────────────────────────────────────────────────── */

const MODES: { id: Theme["mode"]; label: string }[] = [
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
  { id: "system", label: "System" },
];

function AppearancePage() {
  const { value, error, reload } = useResource(() => api.getTheme());
  const [draft, setDraft] = useState<Theme | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const theme = draft ?? value;
  const dirty = draft !== null;

  const preview = (next: Theme) => {
    setDraft(next);
    applyTheme(next.palette, next.mode);
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.putTheme(draft);
      setDraft(null);
      await reload();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const discard = () => {
    setDraft(null);
    if (value) applyTheme(value.palette, value.mode);
  };

  return (
    <>
      <PageHead
        title="Appearance"
        note="pick a palette and a light/dark mode — preview applies immediately, Save keeps it"
      />
      {error && <p className="form-error">{error}</p>}
      <SectionLabel>Palette</SectionLabel>
      <div className="palette-grid">
        {PALETTES.map((p) => (
          <button
            key={p.id}
            type="button"
            className="palette-swatch"
            aria-pressed={theme?.palette === p.id}
            onClick={() => theme && preview({ ...theme, palette: p.id })}
          >
            <span
              className="palette-swatch-dot"
              style={{ background: `linear-gradient(135deg, ${p.bg} 50%, ${p.accent} 50%)` }}
            />
            {p.name}
          </button>
        ))}
      </div>
      <SectionLabel>Mode</SectionLabel>
      <div className="seg" role="radiogroup" aria-label="mode">
        {MODES.map((m) => (
          <label key={m.id} className="seg-opt">
            <input
              type="radio"
              name="theme-mode"
              checked={theme?.mode === m.id}
              onChange={() => theme && preview({ ...theme, mode: m.id })}
            />
            {m.label}
          </label>
        ))}
      </div>
      <SaveRow
        onSave={save}
        onDiscard={discard}
        dirty={dirty}
        busy={busy}
        message={message}
        hint="writes theme.yaml"
      />
    </>
  );
}

/* ── 5f notifications ─────────────────────────────────────────────────────── */

/** The two states Kraft is blocked on a person. Anything else gets muted
 *  within a week, and a muted channel is the same as no channel. */
const NOTIFY_EVENTS: { id: string; label: string }[] = [
  { id: "gate_requested", label: "a decision is waiting" },
  { id: "work_item_needs_human", label: "stopped — gate wait or cap breach" },
];

function NotifyPage() {
  const { value, error, reload } = useResource(() => api.getNotify());
  const [url, setUrl] = useState("");
  const [baseUrl, setBaseUrl] = useState<string | null>(null);
  // `where` is the control that triggered the save — "channel", "url",
  // "base", or an event id (each event switch is its own control) — so a
  // message only ever renders next to the control that produced it, not
  // wherever `message` happens to also be read.
  const [message, setMessage] = useState<{ where: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const notify: Notify | null = value;

  const put = async (body: Parameters<typeof api.putNotify>[0], where: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.putNotify(body);
      setUrl("");
      await reload();
      setMessage({ where, text: "saved" });
    } catch (e) {
      setMessage({ where, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const base = baseUrl ?? notify?.base_url ?? "";

  return (
    <>
      <PageHead
        title="Notifications"
        note="one webhook — ntfy, Pushover, Slack, Discord, or your own receiver"
      />
      {error && <p className="form-error">{error}</p>}
      {notify && (
        <>
          <section className="settings-section">
            <h6>Channel</h6>
            <div className="save-row">
              <button
                className="switch"
                role="switch"
                aria-label="Notifications"
                aria-checked={notify.enabled}
                disabled={busy}
                onClick={() => put({ enabled: !notify.enabled }, "channel")}
              >
                <span className="switch-knob" />
              </button>
              <span className="save-hint">
                {message?.where === "channel"
                  ? message.text
                  : notify.enabled
                    ? "on — Kraft will POST when a run stops"
                    : "off — no outbound traffic"}
              </span>
            </div>
            <div className="field">
              <label htmlFor="notify-url">Webhook URL</label>
              <input
                id="notify-url"
                className="input mono"
                type="password"
                autoComplete="off"
                placeholder={notify.url_set ? "•••••••• (unchanged)" : "https://ntfy.sh/your-topic"}
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
              <span className="field-hint">
                {notify.url_set
                  ? "a webhook URL is set — it is never shown again, because it usually carries a token"
                  : "usually carries a token in its path, so Kraft stores it 0600 and never displays it"}
              </span>
            </div>
            <SaveRow
              onSave={() => put({ url }, "url")}
              onDiscard={() => setUrl("")}
              dirty={url.length > 0}
              busy={busy}
              message={message?.where === "url" ? message.text : null}
              hint="writes notify.yaml, 0600"
            />
            {notify.url_set && (
              <div className="save-row">
                {/* No `data-danger`: the only rule for it is
                    `.overflow-menu button[data-danger]` (styles.css:159), so
                    outside an overflow menu the attribute styles nothing. The
                    action is also cheap to undo — paste the URL again. */}
                <button
                  className="btn btn-ghost"
                  disabled={busy}
                  onClick={() => put({ url: "" }, "url")}
                >
                  Clear URL
                </button>
                <span className="save-hint">removes the stored webhook and stops all sends</span>
              </div>
            )}
          </section>

          <section className="settings-section">
            <h6>Link back</h6>
            <div className="field">
              <label htmlFor="notify-base-url">Base URL</label>
              <input
                id="notify-base-url"
                className="input mono"
                placeholder="http://192.168.1.20:8765"
                value={base}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
              <span className="field-hint">
                What the notification links to. Kraft only knows its bind address, which is
                0.0.0.0 on the LAN — set the address your phone can actually reach.
              </span>
            </div>
            <SaveRow
              onSave={() => put({ base_url: base }, "base")}
              onDiscard={() => setBaseUrl(null)}
              dirty={baseUrl !== null && baseUrl !== (notify.base_url ?? "")}
              busy={busy}
              message={message?.where === "base" ? message.text : null}
              hint="writes notify.yaml"
            />
          </section>

          <section className="settings-section">
            <h6>What notifies</h6>
            {/* Toggles, not checkboxes: this codebase has no `type="checkbox"`
                anywhere, and `.radio` hides its input to draw a round dot —
                a radio's affordance, which is wrong for a multi-select. The
                `.switch` pattern is already here and already means on/off
                (PluginsPage, Settings.tsx:472-480). */}
            {NOTIFY_EVENTS.map((e) => (
              <div key={e.id} className="save-row">
                <button
                  className="switch"
                  role="switch"
                  aria-checked={notify.events.includes(e.id)}
                  aria-label={e.id}
                  disabled={busy}
                  onClick={() =>
                    put(
                      {
                        events: notify.events.includes(e.id)
                          ? notify.events.filter((x) => x !== e.id)
                          : [...notify.events, e.id],
                      },
                      e.id,
                    )
                  }
                >
                  <span className="switch-knob" />
                </button>
                <span className="save-hint">
                  {message?.where === e.id ? (
                    message.text
                  ) : (
                    <>
                      <code>{e.id}</code> · {e.label}
                    </>
                  )}
                </span>
              </div>
            ))}
            <p className="settings-foot">
              Progress events are deliberately not offered. A notifier that fires on progress
              gets muted, and a muted channel is the same as no channel at all.
            </p>
          </section>
        </>
      )}
    </>
  );
}

/* ── 5e access ────────────────────────────────────────────────────────────── */

function AccessPage() {
  const { value, error, reload } = useResource(() => api.getAccess());
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const access: Access | null = value;

  const loadSessions = useCallback(() => {
    return api
      .getAuthSessions()
      .then((r) => setSessions(r.sessions))
      .catch(() => setSessions([]));
  }, []);
  // wrapped for the same reason `useResource` wraps `reload`: `useEffect` reads a
  // returned promise as a cleanup function.
  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  const put = async (body: Parameters<typeof api.putAccess>[0]) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.putAccess(body);
      setPassword("");
      // `busy` must mean "settled": clearing it before the re-fetch lands
      // re-enables the controls while the page still renders pre-save state.
      await Promise.all([reload(), loadSessions()]);
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHead title="Access" note="auth is off on localhost and on for anything else" />
      {error && <p className="form-error">{error}</p>}
      {access && (
        <>
          <section className="settings-section">
            <h6>Bind</h6>
            <label className="radio">
              <input
                type="radio"
                name="bind"
                checked={access.bind === "127.0.0.1"}
                onChange={() => put({ bind: "127.0.0.1" })}
              />
              <span className="dot" />
              <span>
                127.0.0.1:{access.port}{" "}
                <span className="row-sub">· this machine only, no password</span>
              </span>
            </label>
            <label className="radio">
              <input
                type="radio"
                name="bind"
                checked={access.bind !== "127.0.0.1"}
                onChange={() => put({ bind: "0.0.0.0" })}
              />
              <span className="dot" />
              <span>
                0.0.0.0:{access.port}{" "}
                <span className="row-sub">· LAN, password required — for the phone view</span>
              </span>
            </label>
            <p className="settings-foot">
              Takes effect on restart. Kraft never binds publicly; use a tunnel if you need
              remote access.
            </p>
          </section>

          <section className="settings-section">
            <h6>Password</h6>
            <div className="access-grid">
              <div className="field">
                <label htmlFor="access-password">
                  {access.password_set ? "New password" : "Set a password"}
                </label>
                <input
                  id="access-password"
                  className="input"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              <div className="field">
                <label>Session expiry</label>
                <div className="seg" role="radiogroup" aria-label="session expiry">
                  {[1, 7, 30].map((d) => (
                    <label key={d} className="seg-opt">
                      <input
                        type="radio"
                        name="expiry"
                        checked={access.session_expiry_days === d}
                        onChange={() => put({ session_expiry_days: d })}
                      />
                      {d}d
                    </label>
                  ))}
                </div>
              </div>
            </div>
            <SaveRow
              onSave={() => put({ password })}
              onDiscard={() => setPassword("")}
              dirty={password.length > 0}
              busy={busy}
              message={message}
              hint="writes access.yaml · a new password signs every session out"
            />
          </section>

          <section className="settings-section">
            <h6>Sessions</h6>
            {sessions.length === 0 && <p className="empty">no sessions — auth is off</p>}
            {sessions.map((s) => (
              <Row key={s.id} columns="1fr 130px 80px auto" data-session={s.id}>
                <RowText
                  title={
                    <>
                      {s.label ?? "unknown"}
                      {s.current && <span className="session-current"> current</span>}
                    </>
                  }
                  sub={s.ip}
                />
                <span className="row-sub">seen {ago(s.last_seen_at)}</span>
                <span className="row-sub">expires {until(s.expires_at)}</span>
                <OverflowMenu
                  label={`session ${s.label ?? s.id}`}
                  items={[
                    {
                      label: "Revoke",
                      danger: true,
                      // Revoking the session you are using signs you out of this
                      // browser on the spot, and there is no undo either way.
                      confirm: s.current
                        ? "Revoke this session? It signs you out here."
                        : `Revoke ${s.label ?? "this session"}? That browser has to sign in again.`,
                      onSelect: () =>
                        api
                          .revokeSession(s.id)
                          .then(loadSessions)
                          .catch((e) => setMessage(e instanceof Error ? e.message : String(e))),
                    },
                  ]}
                />
              </Row>
            ))}
          </section>
        </>
      )}
    </>
  );
}

/* ── shell ────────────────────────────────────────────────────────────────── */

export function Settings() {
  return (
    <div className="board settings">
      <aside className="board-sidebar">
        <div className="facet">
          <SectionLabel>Settings</SectionLabel>
          {PAGES.map((p) => (
            <NavLink key={p.to} to={`/settings/${p.to}`} className="facet-opt settings-link">
              {p.label}
            </NavLink>
          ))}
        </div>
        <div className="board-foot">config written to versioned YAML in templates/</div>
      </aside>
      <div className="settings-body">
        <Routes>
          <Route index element={<Navigate to="repos" replace />} />
          <Route path="repos" element={<ReposPage />} />
          <Route path="templates" element={<TemplatesPage />} />
          <Route path="plugins" element={<PluginsPage />} />
          <Route path="steering" element={<SteeringPage />} />
          <Route path="policy" element={<PolicyPage />} />
          <Route path="appearance" element={<AppearancePage />} />
          <Route path="intake" element={<IntakePage />} />
          <Route path="notify" element={<NotifyPage />} />
          <Route path="access" element={<AccessPage />} />
        </Routes>
      </div>
    </div>
  );
}
