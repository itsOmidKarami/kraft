import { useCallback, useEffect, useRef, useState } from "react";
import { ago, plural } from "../../../format";
import type { ForgeAccountRow, ForgeTestAnswer, Repo } from "../../../types";
import { detailOf, request } from "../../http";
import { DEFAULT_REMOVABLE, EMPTY_FORGES, FORGES_LEDE, NAMED_BY, PASTE_ONCE, PHONE_READ_ONLY, PHONE_REMOVE, SSO_LINK, type ForgeAccount, accountsOf, namedBy, probeWords, sourceWords } from "../../settings/forgeWords";
import { showToast } from "../../ui/Toast";
import { ChoiceSheet, ConfirmSheet, EditSheet, useSheet } from "../nav/Sheet";
import { AreaScreen } from "./AreaScreen";
import { Group, type RowSpec } from "./kit";

/** POST /test, then a toast with its first line. The server's probe runs in the background after a token changes, so a
 *  re-read would still show the old state; the test itself answers with the new one. Up to 90 s.
 *  A copy of forgeForm's checkAndToast (the PUT below, of putToken): phone/ may not import settings/forgeForm (the contract test's ALLOWED). */
async function check(name: string, lead = ""): Promise<void> {
  const r = await request<ForgeTestAnswer>(`/forges/${encodeURIComponent(name)}/test`, { method: "POST" });
  if (r.status !== 200) return void showToast(`${lead}${detailOf(r.body)}`.trim(), { tone: "error" });
  showToast(`${lead}${r.body.output.split("\n")[0]}`.trim(), r.body.ok ? undefined : { tone: "error" });
}

/** `/settings/forge-accounts`: the accounts and their last check. Test, paste a new token and remove; adding or editing
 *  an account is the desktop's or the terminal's. */
export function ForgesScreen() {
  const [accounts, setAccounts] = useState<ForgeAccount[] | null>(null);
  const [repos, setRepos] = useState<Pick<Repo, "name" | "forge_account">[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState<string | null>(null);
  const [testing, setTesting] = useState<ReadonlySet<string>>(new Set());
  const shown = useRef<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const sheet = useSheet();
  const load = useCallback(async () => {
    const [f, r] = await Promise.all([request<{ accounts: Record<string, ForgeAccountRow> }>("/forges"), request<{ repos?: Repo[] }>("/repos")]);
    if (f.status !== 200) return setError(detailOf(f.body));
    setError(null);
    setAccounts(accountsOf(f.body));
    setRepos(r.status === 200 ? (r.body.repos ?? []) : []);
  }, []);
  useEffect(() => void load(), [load]);

  shown.current = name;
  const today = new Date();
  const a = accounts?.find((x) => x.name === name);
  const named = a ? namedBy(repos, a.name) : [];
  const path = (n: string) => `/forges/${encodeURIComponent(n)}`;

  const rows = (accounts ?? []).map((x): RowSpec => {
    const s = probeWords(x, today);
    const users = namedBy(repos, x.name);
    // The state is always a chip (toneless when muted, as About's "unknown"); the value is only ever the client.
    const chip = s.word ? [{ label: s.word, tone: s.tone === "muted" ? undefined : (s.tone as "ok" | "warn" | "bad") }] : undefined;
    const sub = [...new Set([x.forge, x.web_url, sourceWords(x), s.ssoUrl ? null : s.line, x.probe && `checked ${ago(x.probe.checked_at)}`, users.length && `used by ${users.join(", ")}`].filter(Boolean))].join(" · ");
    return { key: x.name, label: x.name, mono: true, value: x.client, chips: chip, sub, onClick: () => { setName(x.name); sheet.open("menu"); } };
  });

  /** One account's check, flagged on that account alone. */
  const probe = async (n: string, lead?: string) => {
    setTesting((t) => new Set(t).add(n));
    await check(n, lead);
    setTesting((t) => { const u = new Set(t); u.delete(n); return u; });
  };
  const test = async (n: string) => {
    await probe(n);
    // Only this account's own sheet: another one opened meanwhile is not ours to close.
    if (shown.current === n) sheet.close();
    void load();
  };
  const paste = async (n: string, kind: string, value: string) => {
    if (!value) return;
    setBusy(true);
    const r = await request(`${path(n)}/token`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ value, kind }) });
    setBusy(false);
    // Any answer closes the sheet, so the token never stays in a field a refusal could leave on screen.
    sheet.close();
    if (r.status !== 200) return void showToast(detailOf(r.body), { tone: "error" });
    await probe(n, `Pasted a new token for ${n}. `);
    void load();
  };
  const remove = async (n: string) => {
    setBusy(true);
    setRemoveError(null);
    const r = await request(path(n), { method: "DELETE" });
    setBusy(false);
    if (r.status !== 204) return setRemoveError(detailOf(r.body));
    sheet.close();
    showToast(`Removed ${n}`);
    void load();
  };

  // Paste is offered only on an api account whose token is not a command: PUT /token would erase a command. The server
  // refuses it too (403); the sheet leaves it out so a person never meets that. Do not "simplify" it away.
  const pasteable = a?.client === "api" && a.token?.from !== "command";
  const s = a ? probeWords(a, today) : null;

  return (
    <AreaScreen title="Forge accounts" sub={FORGES_LEDE} yaml={false}>
      {error && <p className="ph-error" role="alert">{error}</p>}
      {accounts && (accounts.length === 0 ? <Group note={EMPTY_FORGES} foot={PHONE_READ_ONLY} /> : <Group title={plural(accounts.length, "account")} rows={rows} foot={PHONE_READ_ONLY} />)}
      {a && s && sheet.is("menu") && (
        <ChoiceSheet
          title={a.name}
          text={named.length ? NAMED_BY(named) : a.default_for_host ? DEFAULT_REMOVABLE : undefined}
          options={[
            ...(s.ssoUrl ? [{ value: "sso", label: SSO_LINK }] : []),
            { value: "test", label: testing.has(a.name) ? "Testing…" : "Test", disabled: testing.has(a.name) },
            ...(pasteable ? [{ value: "paste", label: "Paste a new token" }] : []),
            ...(named.length ? [] : [{ value: "remove", label: "Remove", danger: true }]),
          ]}
          onPick={(v) => {
            setRemoveError(null);
            if (v === "sso") {
              window.open(s.ssoUrl!, "_blank", "noopener");
              sheet.close();
            } else if (v === "test") void test(a.name);
            else sheet.swap(v);
          }}
          onClose={sheet.close}
        />
      )}
      {a && pasteable && sheet.is("paste") && (
        <EditSheet title={`Paste a new token for ${a.name}`} text={PASTE_ONCE} secret submitLabel="Replace" busy={busy} onSubmit={(v) => void paste(a.name, a.token?.kind ?? "unknown", v)} onClose={sheet.close} />
      )}
      {a && !named.length && sheet.is("remove") && (
        <ConfirmSheet title={`Remove ${a.name}?`} text={PHONE_REMOVE} busy={busy} error={removeError} confirm={{ label: "Remove", danger: true, run: () => void remove(a.name) }} onClose={sheet.close} />
      )}
    </AreaScreen>
  );
}
