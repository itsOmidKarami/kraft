import { useEffect, useState } from "react";
import type { Machine } from "../../types";
import { detailOf, jsonBody, request } from "../http";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field } from "../ui/Field";
import { showToast } from "../ui/Toast";
import { Switch } from "../ui/Switch";
import { WorkerTokenDialog } from "./WorkerTokenDialog";
import { capacityValue, CapacityField, GiveUpField, GroupsField, ReposField, useRepoChoices, type RepoScope } from "./workerForm";
import { isLocal } from "./workersWords";

type Rotated = { serve_command: string; note: string };

const same = (a: string[], b: string[]) => a.length === b.length && a.every((x, i) => x === b[i]);

/** Edit a machine. It reads `/workers` again when it opens, so what blocks a removal is current. */
export function EditWorkerDialog({ name, onClose, onSaved, onRemove }: { name: string; onClose: () => void; onSaved: () => void; onRemove: (name: string) => void }) {
  const [machine, setMachine] = useState<Machine | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void request<{ workers: Machine[] }>("/workers").then((r) => {
      if (!live) return;
      if (r.status !== 200) return setError(detailOf(r.body));
      const found = r.body.workers.find((m) => m.name === name);
      if (found) setMachine(found);
      else setError(`${name} is not in the list any more`);
    });
    return () => { live = false; };
  }, [name]);
  if (machine) return <EditForm machine={machine} onClose={onClose} onSaved={onSaved} onRemove={onRemove} />;
  return (
    <Dialog title={`Edit ${name}`} onClose={onClose} className="set-wk-dlg" footer={<Button onClick={onClose}>Close</Button>}>
      {error ? <p className="set-error" role="alert">{error}</p> : <p className="set-hint" role="status">Reading {name}…</p>}
    </Dialog>
  );
}

function EditForm({ machine: m, onClose, onSaved, onRemove }: { machine: Machine; onClose: () => void; onSaved: () => void; onRemove: (name: string) => void }) {
  const local = isLocal(m);
  const [enabled, setEnabled] = useState(m.enabled);
  const [capacity, setCapacity] = useState(m.configured_capacity === null ? "" : String(m.configured_capacity));
  const [giveUp, setGiveUp] = useState(m.give_up_after);
  const [groups, setGroups] = useState(m.groups);
  const [scope, setScope] = useState<RepoScope>(m.repos?.length ? "some" : "every");
  const [repos, setRepos] = useState(m.repos ?? []);
  const [ssh, setSsh] = useState(m.ssh ?? "");
  const [confirming, setConfirming] = useState(false);
  const [rotated, setRotated] = useState<Rotated | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const choices = useRepoChoices();

  const newCapacity = capacityValue(capacity) ?? null;
  const changes: Record<string, unknown> = {
    ...(enabled !== m.enabled && { enabled }),
    ...(newCapacity !== m.configured_capacity && { capacity: newCapacity }),
    ...(!local && giveUp !== m.give_up_after && { give_up_after: giveUp }),
    ...(!same(groups, m.groups) && { groups }),
    ...(!local && (scope === "every" ? m.repos !== null : !m.repos || !same(repos, m.repos)) && { repos: scope === "every" ? null : repos }),
    ...(m.ssh !== null && ssh.trim() !== m.ssh && { ssh: ssh.trim() }),
  };
  const changed = Object.keys(changes).length > 0;

  const save = async () => {
    setBusy(true);
    setError(null);
    const r = await request(`/workers/${encodeURIComponent(m.name)}`, jsonBody("PATCH", changes));
    setBusy(false);
    if (r.status !== 200) return setError(detailOf(r.body));
    onSaved();
    showToast(`Saved ${m.name}. Changes apply from the next step.`);
    onClose();
  };
  const rotate = async () => {
    setBusy(true);
    setError(null);
    const r = await request<Rotated>(`/workers/${encodeURIComponent(m.name)}/rotate-token`, { method: "POST" });
    setBusy(false);
    setConfirming(false);
    if (r.status !== 200) return setError(detailOf(r.body));
    onSaved();
    setRotated(r.body);
  };

  if (rotated) return <WorkerTokenDialog name={m.name} title={`New token for ${m.name}`} serveCommand={rotated.serve_command} note={rotated.note} onClose={onClose} />;

  const capacityHint = local
    ? `In use: ${m.capacity === null ? "no limit" : m.capacity}. Blank: no limit.`
    : [`In use: ${m.capacity}.`, m.machine_capacity != null && `The machine's own limit is ${m.machine_capacity}.`, "Blank: the machine's own limit, or 1."].filter(Boolean).join(" ");
  return (
    <Dialog
      title={`Edit ${m.name}`}
      onClose={onClose}
      dirty={busy || changed}
      className="set-wk-dlg"
      footer={
        <div className="set-wk-foot">
          <div className="set-wk-foot-row">
            {!local && <Button variant="danger" className="set-wk-remove" disabled={busy || m.remove_blocked !== null} aria-describedby={m.remove_blocked ? "set-wk-blocked" : undefined} onClick={() => { onClose(); onRemove(m.name); }}>Remove machine</Button>}
            <Button onClick={onClose} disabled={busy}>Cancel</Button>
            <Button variant="primary" disabled={!changed || busy} onClick={save}>Save</Button>
          </div>
          {!local && m.remove_blocked && <p id="set-wk-blocked" className="set-hint">{m.remove_blocked}</p>}
        </div>
      }
    >
      <form className="set-wk-form" onSubmit={(e) => { e.preventDefault(); if (changed && !busy) void save(); }}>
        {error && <p className="set-error" role="alert">{error}</p>}
        <div className="set-wk-switch">
          <Switch label="Takes new steps" checked={enabled} onChange={setEnabled} aria-describedby="set-wk-takes" />
          <span className="field-label">Takes new steps</span>
          <span id="set-wk-takes" className="field-hint">Off: running steps finish, nothing new starts.</span>
        </div>
        <div className="set-wk-pair">
          <CapacityField text={capacity} onChange={setCapacity} hint={capacityHint} />
          {!local && <GiveUpField value={giveUp} onChange={setGiveUp} hint="How long a step waits for the machine to come back. Applies when the machine next connects." />}
        </div>
        <GroupsField groups={groups} onChange={setGroups} />
        {!local && <ReposField scope={scope} repos={repos} choices={choices} onScope={setScope} onRepos={setRepos} />}
        {m.ssh !== null && (
          <Field label="ssh host" hint="A new host drops the connection, so it is refused while a step runs on the machine.">
            <input value={ssh} onChange={(e) => setSsh(e.target.value)} />
          </Field>
        )}
        {m.mode === "dial-in" && (
          <div className="set-wk-field">
            <span className="field-label">Token</span>
            {confirming ? (
              <div className="set-wk-rotate">
                <Button variant="danger" disabled={busy} onClick={rotate}>Rotate now</Button>
                <Button disabled={busy} onClick={() => setConfirming(false)}>Keep it</Button>
              </div>
            ) : (
              <div className="set-wk-rotate"><Button disabled={busy} onClick={() => setConfirming(true)}>Rotate token</Button></div>
            )}
            <span className="field-hint">Rotating stops the current token at once; run serve again with the new one. Until then the machine is offline, and its running steps keep going up to its give-up time.</span>
          </div>
        )}
        <p className="set-hint">Changes apply from the next step; running steps finish where they are.</p>
      </form>
    </Dialog>
  );
}
