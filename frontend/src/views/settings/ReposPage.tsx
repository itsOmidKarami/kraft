import { useEffect, useState } from "react";
import { Check, Plus, WarningCircle } from "@phosphor-icons/react";
import * as api from "../../api";
import { OverflowMenu, Row, RowText } from "../../components/ui";
import { backdropProps, useModal } from "../../useModal";
import type { Repo, RepoProbe, TemplateSummary } from "../../types";
import { PageHead, useResource } from "./shared";

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

export function ReposPage() {
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
