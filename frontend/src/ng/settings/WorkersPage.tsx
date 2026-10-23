import { useEffect, useState } from "react";
import { ago, plural } from "../../format";
import type { Machine } from "../../types";
import { detailOf, request } from "../http";
import { Badge } from "../ui/Badge";
import { Block } from "./parts";
import { ADD_COMMAND, EVERY_REPO, NO_MACHINES, READ_ONLY_NOTE, giveUpWords, slotsWords, stateWords } from "./workersWords";
import "./settings.css";

/** Settings › Workers: the machines `kraft admin worker list` prints, read-only. Adding one is the
 *  terminal's and the MCP tools' job. */
export function WorkersPage() {
  const [machines, setMachines] = useState<Machine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void request<{ workers: Machine[] }>("/workers").then((r) => {
      if (r.status !== 200) return setError(detailOf(r.body));
      setMachines(r.body.workers);
    });
  }, []);

  return (
    <div className="ng-settings">
      <div className="set-page is-cards">
        <div className="set-title">
          <h1>Workers</h1>
          <p className="lede">The machines that run work item steps, and whether each is reachable.</p>
        </div>
        {error && <p className="set-error" role="alert">{error}</p>}
        {machines && (
          <Block id="set-wk-machines" title="Machines" card aside={plural(machines.length, "machine")}>
            <div className="set-st-body">
              {machines.length === 0 ? (
                <p className="set-hint">{NO_MACHINES} <code className="set-mono">{ADD_COMMAND}</code></p>
              ) : (
                <table className="set-st-table" aria-label="Machines">
                  <thead>
                    <tr>
                      <th scope="col">Name</th>
                      <th scope="col">Mode</th>
                      <th scope="col">State</th>
                      <th scope="col" className="is-num">Slots</th>
                      <th scope="col">Repos</th>
                      <th scope="col">Last seen</th>
                      <th scope="col">Kraft</th>
                      <th scope="col">Give up</th>
                    </tr>
                  </thead>
                  <tbody>
                    {machines.map((m) => (
                      <tr key={m.name}>
                        <td className="set-mono">{m.name}</td>
                        <td>{m.mode}</td>
                        <td>{m.online ? <Badge tone="ok">{stateWords(m)}</Badge> : stateWords(m)}</td>
                        <td className="is-num">{slotsWords(m)}</td>
                        <td>{m.repos?.length ? m.repos.join(", ") : EVERY_REPO}</td>
                        <td>{m.last_seen ? ago(m.last_seen) : "never"}</td>
                        <td className="set-mono">{m.kraft_version ?? ""}</td>
                        <td>{giveUpWords(m.give_up_after)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <p className="set-hint">{READ_ONLY_NOTE}</p>
            </div>
          </Block>
        )}
      </div>
    </div>
  );
}
