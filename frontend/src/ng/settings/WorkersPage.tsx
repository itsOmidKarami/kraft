import { useCallback, useEffect, useState } from "react";
import { plural } from "../../format";
import type { Machine } from "../../types";
import { detailOf, request } from "../http";
import { sendLater, UNDO_WINDOW_MS } from "../later";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { showToast } from "../ui/Toast";
import { AddWorkerDialog } from "./AddWorkerDialog";
import { EditWorkerDialog } from "./EditWorkerDialog";
import { Block } from "./parts";
import { EVERY_REPO, modeWords, slotsWords, stateReason, stateWord } from "./workersWords";

const Dash = () => <span className="set-wk-dash">—</span>;
import "./settings.css";

const STATE_TONE = { online: "ok", offline: "muted", disabled: "muted" } as const;

/** Settings › Workers: the machines `kraft admin worker list` prints, with the same operations
 *  (add, edit, enable or disable, remove). The server enforces every rule and this page shows its words. */
export function WorkersPage() {
  const [machines, setMachines] = useState<Machine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  // Machines taken off the page for the undo window: a re-read inside it must not bring them back.
  const [removing, setRemoving] = useState<string[]>([]);
  const load = useCallback(async () => {
    const r = await request<{ workers: Machine[] }>("/workers");
    if (r.status !== 200) return setError(detailOf(r.body));
    setError(null);
    setMachines(r.body.workers);
  }, []);
  useEffect(() => void load(), [load]);

  /** Remove `name` after the undo window, or at once if the page is left. The server refuses it
   *  then if a step started on the machine meanwhile: the row comes back, with its reason. */
  const remove = (name: string) => {
    setRemoving((r) => [...r, name]);
    const done = () => setRemoving((r) => r.filter((n) => n !== name));
    let refused = false;
    const { undo } = sendLater(async ({ keepalive }) => {
      const r = await request(`/workers/${encodeURIComponent(name)}`, { method: "DELETE", ...(keepalive && { keepalive }) });
      refused = r.status !== 200;
      done();
      if (refused) showToast(detailOf(r.body), { tone: "error" });
      void load();
    }, UNDO_WINDOW_MS);
    showToast(`Removed ${name}`, {
      ms: UNDO_WINDOW_MS,
      action: { label: "Undo", run: () => { if (undo()) done(); else if (!refused) showToast(`Too late to undo: ${name} was removed.`); } },
    });
  };

  const shown = machines?.filter((m) => !removing.includes(m.name));
  const disabled = shown?.filter((m) => !m.enabled).length ?? 0;
  return (
    <div className="ng-settings">
      <div className="set-page is-cards">
        <div className="set-title">
          <h1>Workers</h1>
          <p className="lede">The machines that run work item steps, and whether each is reachable.</p>
        </div>
        {error && <p className="set-error" role="alert">{error}</p>}
        {shown && (
          <Block
            id="set-wk-machines"
            title="Machines"
            card
            aside={`${plural(shown.length, "machine")}${disabled ? ` · ${disabled} disabled` : ""}`}
            action={<Button onClick={() => setAdding(true)}>Add machine</Button>}
          >
            <div className="set-wk-body">
              {shown.length === 0 ? (
                <p className="set-hint">No machines yet. Add one with Add machine.</p>
              ) : (
                <div className="set-wk-scroll">
                  <table className="set-wk-table" aria-label="Machines">
                    <thead>
                      <tr>
                        <th scope="col">Name</th>
                        <th scope="col">Connects</th>
                        <th scope="col">State</th>
                        <th scope="col" className="is-num">Running</th>
                        <th scope="col">Groups</th>
                        <th scope="col">Repos</th>
                        <th scope="col">Version</th>
                        <th scope="col"><span className="adr-sr">Actions</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((m) => (
                        <tr key={m.name} className={m.enabled ? undefined : "is-off"}>
                          <td className="set-mono is-dim">{m.name}</td>
                          <td className="is-dim">{modeWords(m)}</td>
                          <td>
                            <div className="set-wk-state">
                              <Badge tone={STATE_TONE[stateWord(m)]}>{stateWord(m)}</Badge>
                              {stateReason(m) && <span className="set-wk-why">{stateReason(m)}</span>}
                            </div>
                          </td>
                          <td className="is-num is-dim">{slotsWords(m)}</td>
                          <td className="is-dim">{m.groups.length ? m.groups.join(", ") : <Dash />}</td>
                          <td className="is-dim">{m.repos?.length ? m.repos.join(", ") : EVERY_REPO}</td>
                          <td className="set-mono is-dim">{m.kraft_version ?? <Dash />}</td>
                          <td className="is-act"><Button onClick={() => setEditing(m.name)} aria-label={`Edit ${m.name}`}>Edit</Button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </Block>
        )}
        {shown && (
          <p className="set-hint set-wk-note">
            The terminal does the same with <code className="set-mono">kraft admin worker</code>, and both write <code className="set-mono">workers.yaml</code>.
          </p>
        )}
      </div>
      {editing && <EditWorkerDialog name={editing} onClose={() => setEditing(null)} onSaved={() => void load()} onRemove={remove} />}
      {adding && <AddWorkerDialog onClose={() => setAdding(false)} onAdded={() => void load()} onEdit={setEditing} />}
    </div>
  );
}
