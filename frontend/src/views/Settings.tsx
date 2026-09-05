import { useCallback, useEffect, useMemo, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { Check, Plus, WarningCircle } from "@phosphor-icons/react";
import * as api from "../api";
import { ago, until } from "../format";
import { OverflowMenu, Row, RowText, SectionLabel } from "../components/ui";
import { useModal } from "../useModal";
import type {
  Access,
  AuthSession,
  HookBinding,
  Policy,
  Repo,
  RepoProbe,
  TemplateSummary,
  TemplateValidation,
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
  { to: "policy", label: "Policy" },
  { to: "access", label: "Access" },
];

/** Load-once-then-edit, the shape every page here needs. */
function useResource<T>(load: () => Promise<T>) {
  const [value, setValue] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    load()
      .then((v) => {
        setValue(v);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    // `load` is redefined every render by design — the caller closes over its own
    // state — so it is deliberately not a dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(reload, [reload]);
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
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="Add repo">
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
      reload();
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
          {report && (
            <div className="validation" data-valid={report.valid}>
              <span>{report.valid ? "valid" : report.error}</span>
              {report.by_repo.map((r) => (
                <span key={r.repo} className="row-sub">
                  {r.repo}: {r.resolvable ? "resolvable" : "unresolvable"}
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
      reload();
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
      reload();
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

/* ── 5e access ────────────────────────────────────────────────────────────── */

function AccessPage() {
  const { value, error, reload } = useResource(() => api.getAccess());
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const access: Access | null = value;

  const loadSessions = useCallback(() => {
    api
      .getAuthSessions()
      .then((r) => setSessions(r.sessions))
      .catch(() => setSessions([]));
  }, []);
  useEffect(loadSessions, [loadSessions]);

  const put = async (body: Parameters<typeof api.putAccess>[0]) => {
    setBusy(true);
    setMessage(null);
    try {
      await api.putAccess(body);
      setPassword("");
      reload();
      loadSessions();
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
  const nav = useNavigate();
  useEffect(() => {
    // land on a page rather than an empty shell
    if (window.location.pathname === "/settings") nav("/settings/repos", { replace: true });
  }, [nav]);

  return (
    <div className="board settings">
      <aside className="board-sidebar">
        <div className="facet">
          <SectionLabel>Settings</SectionLabel>
          {PAGES.map((p) => (
            <NavLink key={p.to} to={p.to} className="facet-opt settings-link">
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
          <Route path="policy" element={<PolicyPage />} />
          <Route path="access" element={<AccessPage />} />
        </Routes>
      </div>
    </div>
  );
}
