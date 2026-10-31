import { useCallback, useEffect, useState } from "react";
import { ago, plural } from "../../format";
import type { ForgeAccountRow, Repo } from "../../types";
import { detailOf, request } from "../http";
import { sendLater, UNDO_WINDOW_MS } from "../later";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Empty } from "../ui/Empty";
import { LinkButton } from "../ui/LinkButton";
import { showToast } from "../ui/Toast";
import { type ForgeAccount, accountsOf, EMPTY_FORGES, EMPTY_FORGES_WHY, FORGES_LEDE, namedBy, probeWords, SSO_LINK, sourceWords, TEST_TO_CHECK } from "./forgeWords";
import { AddForgeDialog } from "./AddForgeDialog";
import { EditForgeDialog } from "./EditForgeDialog";
import { Block } from "./parts";
import "./settings.css";

const Dash = () => <span className="set-fg-dash">—</span>;

/** The accounts, the repos that name them, and `remove` with its 5 s undo window (Workers' pattern; the DELETE answers 204). */
export function useForges() {
  const [accounts, setAccounts] = useState<ForgeAccount[] | null>(null);
  const [repos, setRepos] = useState<Pick<Repo, "name" | "forge_account">[]>([]);
  const [error, setError] = useState<string | null>(null);
  // Accounts taken off the page for the undo window: a re-read inside it must not bring them back.
  const [removing, setRemoving] = useState<string[]>([]);
  const load = useCallback(async () => {
    const [f, r] = await Promise.all([request<{ accounts: Record<string, ForgeAccountRow> }>("/forges"), request<{ repos?: Repo[] }>("/repos")]);
    if (f.status !== 200) return setError(detailOf(f.body));
    setError(null);
    setAccounts(accountsOf(f.body));
    setRepos(r.status === 200 ? (r.body.repos ?? []) : []);
  }, []);
  useEffect(() => void load(), [load]);

  /** Remove `name` after the undo window, or at once if the page is left. A 409 then (a repo named the account
   *  meanwhile) brings the row back with the server's words, which list the repos. */
  const remove = (name: string) => {
    setRemoving((r) => [...r, name]);
    const done = () => setRemoving((r) => r.filter((n) => n !== name));
    // Where the DELETE is: Undo says one thing for each, and nothing once the server has refused (its toast is up).
    let phase: "waiting" | "sent" | "gone" | "refused" = "waiting";
    const { undo } = sendLater(async ({ keepalive }) => {
      phase = "sent";
      const r = await request(`/forges/${encodeURIComponent(name)}`, { method: "DELETE", ...(keepalive && { keepalive }) });
      phase = r.status === 204 ? "gone" : "refused";
      done();
      if (phase === "refused") showToast(detailOf(r.body), { tone: "error" });
      void load();
    }, UNDO_WINDOW_MS);
    showToast(`Removed ${name}`, {
      ms: UNDO_WINDOW_MS,
      action: { label: "Undo", run: () => {
        if (undo()) done();
        else if (phase === "sent") showToast(`${name} is being removed.`);
        else if (phase === "gone") showToast(`Too late to undo: ${name} was removed.`);
      },
    },
    });
  };
  return { accounts: accounts?.filter((a) => !removing.includes(a.name)), repos, error, load, remove };
}

/** Settings › Forge accounts: the accounts `kraft admin forge list` prints and the last check of each token. */
export function ForgesPage() {
  const { accounts, repos, error, load, remove } = useForges();
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const editingAccount = accounts?.find((a) => a.name === editing);
  const today = new Date();
  // A pin to an account that is not in forges.yaml: the repo falls back to gh/glab, so say which.
  const missing = accounts ? repos.filter((r) => r.forge_account && !accounts.some((a) => a.name === r.forge_account)) : [];
  const add = <Button onClick={() => setAdding(true)}>Add account</Button>;
  return (
    <div className="ng-settings">
      <div className="set-page is-cards">
        <div className="set-title">
          <h1>Forge accounts</h1>
          <p className="lede">{FORGES_LEDE}</p>
        </div>
        {error && <p className="set-error" role="alert">{error}</p>}
        {missing.map((r) => (
          <p key={r.name} className="set-hint is-warn">{r.name} names {r.forge_account}, which is not in forges.yaml: it uses gh or glab.</p>
        ))}
        {accounts && accounts.length === 0 && (
          <Empty variant="block" action={add}>
            {EMPTY_FORGES} {EMPTY_FORGES_WHY}
          </Empty>
        )}
        {accounts && accounts.length > 0 && (
          <Block id="set-fg-accounts" title="Accounts" card aside={plural(accounts.length, "account")} action={add}>
            <div className="set-fg-body">
              <div className="set-fg-scroll">
                <table className="set-fg-table" aria-label="Forge accounts">
                  <thead>
                    <tr>
                      <th scope="col">Name</th>
                      <th scope="col">State</th>
                      <th scope="col">Forge</th>
                      <th scope="col">Token</th>
                      <th scope="col">Checked</th>
                      <th scope="col">Used by</th>
                      <th scope="col"><span className="adr-sr">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {accounts.map((a) => {
                      const s = probeWords(a, today);
                      const users = namedBy(repos, a.name);
                      return (
                        <tr key={a.name}>
                          <td className="set-mono is-dim set-fg-clip" title={a.name}>{a.name}</td>
                          <td>
                            <div className="set-fg-state">
                              {s.word && <Badge tone={s.tone}>{s.word}</Badge>}
                              {a.client === "cli" ? (
                                <span className="set-fg-why">{TEST_TO_CHECK}</span>
                              ) : s.ssoUrl ? (
                                <LinkButton inline href={s.ssoUrl} target="_blank" rel="noopener noreferrer">{SSO_LINK}</LinkButton>
                              ) : (
                                s.line && <span className="set-fg-why">{s.line}</span>
                              )}
                            </div>
                          </td>
                          <td className="is-dim">{a.forge} <span className="set-fg-url set-fg-clip" title={a.web_url}>{a.web_url}</span></td>
                          <td className="is-dim set-fg-clip" title={sourceWords(a)}>{sourceWords(a)}</td>
                          <td className="is-dim">{a.probe ? ago(a.probe.checked_at) : <Dash />}</td>
                          <td className="is-dim">{users.length ? users.map((u) => <div key={u} className="set-fg-clip" title={u}>{u}</div>) : <Dash />}</td>
                          <td className="is-act"><Button aria-label={`Edit ${a.name}`} onClick={() => setEditing(a.name)}>Edit</Button></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </Block>
        )}
        {accounts && (
          <p className="set-hint set-fg-note">
            The terminal does the same with <code className="set-mono">kraft admin forge</code>, and both write <code className="set-mono">forges.yaml</code>.
          </p>
        )}
      </div>
      {editingAccount && <EditForgeDialog account={editingAccount} repos={repos} onClose={() => setEditing(null)} onSaved={() => void load()} onRemove={remove} />}
      {adding && <AddForgeDialog onClose={() => setAdding(false)} onAdded={() => void load()} />}
    </div>
  );
}
