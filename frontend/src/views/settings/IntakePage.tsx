import { useEffect, useState } from "react";
import * as api from "../../api";
import { Switch } from "../../components/ui";
import type { Intake, Repo } from "../../types";
import { PageHead, SaveRow, useResource } from "./shared";

/* ── 5d-bis auto-intake ───────────────────────────────────────────────────── */

export function IntakePage() {
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
              <Switch
                checked={intake.enabled}
                onChange={(v) => set("enabled", v)}
                label="Auto-intake"
                disabled={busy}
              />
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
