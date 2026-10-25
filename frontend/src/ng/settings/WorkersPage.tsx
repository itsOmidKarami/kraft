import { useCallback, useEffect, useState } from "react";
import { plural } from "../../format";
import type { Machine } from "../../types";
import { detailOf, request } from "../http";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { AddWorkerDialog } from "./AddWorkerDialog";
import { Block } from "./parts";
import { EVERY_REPO, TERMINAL_NOTE, groupsWords, modeWords, slotsWords, stateReason, stateWord } from "./workersWords";
import "./settings.css";

const STATE_TONE = { online: "ok", offline: "bad", disabled: "muted" } as const;

/** Settings › Workers: the machines `kraft admin worker list` prints, with the same operations
 *  (add, edit, enable or disable, remove). The server enforces every rule and this page shows its words. */
export function WorkersPage() {
  const [machines, setMachines] = useState<Machine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [, setEditing] = useState<string | null>(null);
  const load = useCallback(async () => {
    const r = await request<{ workers: Machine[] }>("/workers");
    if (r.status !== 200) return setError(detailOf(r.body));
    setError(null);
    setMachines(r.body.workers);
  }, []);
  useEffect(() => void load(), [load]);

  const disabled = machines?.filter((m) => !m.enabled).length ?? 0;
  return (
    <div className="ng-settings">
      <div className="set-page is-cards">
        <div className="set-title">
          <h1>Workers</h1>
          <p className="lede">The machines that run work item steps, and whether each is reachable.</p>
        </div>
        {error && <p className="set-error" role="alert">{error}</p>}
        {machines && (
          <Block
            id="set-wk-machines"
            title="Machines"
            card
            aside={`${plural(machines.length, "machine")}${disabled ? ` · ${disabled} disabled` : ""}`}
            action={<Button onClick={() => setAdding(true)}>Add machine</Button>}
          >
            <div className="set-wk-body">
              {machines.length === 0 ? (
                <p className="set-hint">No machines yet. Add one with Add machine.</p>
              ) : (
                <div className="set-wk-scroll">
                  <table className="set-wk-table" aria-label="Machines">
                    <thead>
                      <tr>
                        <th scope="col">Name</th>
                        <th scope="col">Connects</th>
                        <th scope="col">State</th>
                        <th scope="col">Running</th>
                        <th scope="col">Groups</th>
                        <th scope="col">Repos</th>
                        <th scope="col">Version</th>
                        <th scope="col"><span className="adr-sr">Actions</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {machines.map((m) => (
                        <tr key={m.name}>
                          <td className="set-mono">{m.name}</td>
                          <td>{modeWords(m)}</td>
                          <td>
                            <div className="set-wk-state">
                              <Badge tone={STATE_TONE[stateWord(m)]}>{stateWord(m)}</Badge>
                              {stateReason(m) && <span className="set-wk-why">{stateReason(m)}</span>}
                            </div>
                          </td>
                          <td className="is-num">{slotsWords(m)}</td>
                          <td>{groupsWords(m)}</td>
                          <td>{m.repos?.length ? m.repos.join(", ") : EVERY_REPO}</td>
                          <td className="set-mono">{m.kraft_version ?? ""}</td>
                          <td className="is-act"><Button onClick={() => setEditing(m.name)} aria-label={`Edit ${m.name}`}>Edit</Button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="set-hint">{TERMINAL_NOTE}</p>
            </div>
          </Block>
        )}
      </div>
      {adding && <AddWorkerDialog onClose={() => setAdding(false)} onAdded={() => void load()} onEdit={setEditing} />}
    </div>
  );
}
