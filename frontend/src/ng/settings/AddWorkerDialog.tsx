import { useState } from "react";
import type { Machine } from "../../types";
import { detailOf, jsonBody, request } from "../http";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field } from "../ui/Field";
import { Segmented } from "../ui/Segmented";
import { WorkerTokenDialog } from "./WorkerTokenDialog";
import { capacityValue, CapacityField, GroupsField, ReposField, useRepoChoices, type RepoScope } from "./workerForm";

type Probe = { connected: boolean; detail: string };
type Answer = { worker: Machine; probe?: Probe; serve_command?: string; note?: string };

const NAME_RULE = "Lowercase letters, digits, - or _, 1 to 39 characters.";

/** Add a machine: Kraft connects over ssh (and connects once now), or the machine dials in with a token shown once. */
export function AddWorkerDialog({ onClose, onAdded, onEdit }: { onClose: () => void; onAdded: () => void; onEdit: (name: string) => void }) {
  const [name, setName] = useState("");
  const [how, setHow] = useState<"ssh" | "dial-in">("ssh");
  const [ssh, setSsh] = useState("");
  const [capacity, setCapacity] = useState("");
  const [giveUp, setGiveUp] = useState("");
  const [groups, setGroups] = useState<string[]>([]);
  const [scope, setScope] = useState<RepoScope>("every");
  const [repos, setRepos] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState<Answer | null>(null);
  const choices = useRepoChoices();

  const valid = name.trim() !== "" && (how === "dial-in" || ssh.trim() !== "");
  const save = async () => {
    setBusy(true);
    setError(null);
    const cap = capacityValue(capacity);
    const r = await request<Answer>("/workers", jsonBody("POST", {
      name: name.trim(),
      ...(how === "ssh" && { ssh: ssh.trim() }),
      ...(cap !== undefined && { capacity: cap }),
      ...(giveUp.trim() && { give_up_after: giveUp.trim() }),
      ...(groups.length && { groups }),
      ...(scope === "some" && { repos }),
    }));
    setBusy(false);
    if (r.status !== 201) return setError(detailOf(r.body));
    onAdded();
    setAdded(r.body);
  };

  if (added?.serve_command !== undefined) return <WorkerTokenDialog name={added.worker.name} serveCommand={added.serve_command} note={added.note ?? ""} onClose={onClose} />;
  if (added?.probe) {
    const { worker, probe } = added;
    return (
      <Dialog
        title={`${worker.name} is added`}
        onClose={onClose}
        className="set-wk-dlg"
        footer={<>
          {!probe.connected && <Button onClick={() => { onClose(); onEdit(worker.name); }}>Edit {worker.name}</Button>}
          <Button variant="primary" onClick={onClose}>Done</Button>
        </>}
      >
        {probe.connected ? (
          <p className="set-wk-lead"><Badge tone="ok">connected</Badge> Kraft reached {worker.name} over ssh.</p>
        ) : (
          <div className="set-wk-token">
            <p className="set-wk-lead"><Badge>not connected</Badge> {worker.name} is saved, but Kraft could not connect to it.</p>
            <pre className="set-wk-cmd" aria-label="The ssh error">{probe.detail}</pre>
            <p className="set-hint">Kraft keeps trying. To see what is missing, run <code className="set-mono">kraft admin worker check</code> on the machine.</p>
          </div>
        )}
      </Dialog>
    );
  }

  return (
    <Dialog
      title="Add machine"
      onClose={onClose}
      dirty={busy}
      className="set-wk-dlg"
      footer={<>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="primary" disabled={!valid || busy} onClick={save}>{how === "ssh" ? (busy ? "Connecting…" : "Add and connect") : "Add machine"}</Button>
      </>}
    >
      <form className="set-wk-form" onSubmit={(e) => { e.preventDefault(); if (valid && !busy) void save(); }}>
        {error && <p className="set-error" role="alert">{error}</p>}
        <Field label="Name" hint={NAME_RULE}>
          <input data-autofocus value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <div className="set-wk-field">
          <span className="field-label">Connects</span>
          <Segmented label="Connects" value={how} onChange={setHow} options={[{ value: "ssh", label: "Kraft connects over ssh" }, { value: "dial-in", label: "It dials in to Kraft" }]} />
        </div>
        {how === "ssh" && (
          <Field label="ssh host" hint="As ssh takes it: a name from ~/.ssh/config, or user@host.">
            <input value={ssh} onChange={(e) => setSsh(e.target.value)} />
          </Field>
        )}
        <CapacityField text={capacity} onChange={setCapacity} hint="Blank: the machine's own limit, or 1." />
        <Field label="Give up after" hint="How long a step waits for the machine to come back: 2h, 30m, never. Blank: 2h.">
          <input value={giveUp} onChange={(e) => setGiveUp(e.target.value)} />
        </Field>
        <GroupsField groups={groups} onChange={setGroups} />
        <ReposField scope={scope} repos={repos} choices={choices} onScope={setScope} onRepos={setRepos} />
      </form>
    </Dialog>
  );
}
