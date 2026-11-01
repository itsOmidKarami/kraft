import { useEffect, useRef, useState } from "react";
import type { Repo } from "../../types";
import { detailOf, jsonBody, request } from "../http";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field } from "../ui/Field";
import { Segmented } from "../ui/Segmented";
import { Switch } from "../ui/Switch";
import { showToast } from "../ui/Toast";
import { checkAndToast, CommandSource, KindSelect, type Kind, PasteInput, putToken, sourceBody, type SourceChoice, TokenSourceField } from "./forgeForm";
import { DEFAULT_REMOVABLE, type ForgeAccount, FIXED_FIELDS, NAMED_BY, namedBy, SWITCH_DELETES_PASTED, TO_CLI_DROPS } from "./forgeWords";

/** Edit an account. Name, forge and web address are fixed; a Save sends only the keys that changed. */
export function EditForgeDialog({ account: a, repos, onClose, onSaved, onRemove }: {
  account: ForgeAccount; repos: Pick<Repo, "name" | "forge_account">[]; onClose: () => void; onSaved: () => void; onRemove: (name: string) => void;
}) {
  const t = a.token;
  const command = t?.from === "command";
  const first: SourceChoice = t?.from === "env" ? "env" : t?.from === "file" ? "file" : "git-credential";
  const [client, setClient] = useState(a.client);
  const [apiUrl, setApiUrl] = useState(a.api_url);
  const [isDefault, setDefault] = useState(a.default_for_host);
  const [caFile, setCaFile] = useState(a.ca_file ?? "");
  const [choice, setChoice] = useState<SourceChoice>(first);
  const [env, setEnv] = useState(t?.env ?? "");
  const [file, setFile] = useState(t?.file ?? "");
  const [kind, setKind] = useState<Kind>(t?.kind ?? "unknown");
  const [paste, setPaste] = useState("");
  const [pasteKind, setPasteKind] = useState<Kind>(t?.kind ?? "unknown");
  const [output, setOutput] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Set once the Save or Replace has answered: nothing is left to lose, so Esc and the backdrop close like Cancel.
  const [answered, setAnswered] = useState(false);
  // A person who closed the dialog during a check must not have the late answer close whatever they opened next.
  const closed = useRef(false);
  const close = () => { closed.current = true; onClose(); };
  // The dialog focuses its first control, which would be the unchosen "cli": open on the chosen Client instead (this
  // effect runs after the dialog's own). A command account's Client is disabled, so focus stays where the dialog put it.
  const clientRef = useRef<HTMLDivElement>(null);
  useEffect(() => clientRef.current?.querySelector<HTMLElement>('[aria-checked="true"]:not(:disabled)')?.focus(), []);
  // The output lands below a long form, inside the scrolling body: bring it into view.
  const outRef = useRef<HTMLPreElement>(null);
  useEffect(() => outRef.current?.scrollIntoView?.({ block: "nearest" }), [output]);
  const path = `/forges/${encodeURIComponent(a.name)}`;

  const api = client === "api";
  const tokenChanged = !command && api && (client !== a.client || choice !== first || env !== (t?.env ?? "") || file !== (t?.file ?? "") || kind !== (t?.kind ?? "unknown"));
  // A command account sends neither `token` nor `client`: the server also refuses them (403), and the UI hides the controls so a person never meets that.
  const changes: Record<string, unknown> = {
    ...(!command && client !== a.client && { client }),
    ...(apiUrl !== a.api_url && { api_url: apiUrl.trim() || null }),
    ...(isDefault !== a.default_for_host && { default_for_host: isDefault }),
    ...(api && caFile !== (a.ca_file ?? "") && { ca_file: caFile.trim() || null }),
    ...(tokenChanged && { token: sourceBody(choice, env, file, kind) }),
  };
  const changed = Object.keys(changes).length > 0;
  const named = namedBy(repos, a.name);
  const leavesFile = t?.from === "file" && (client === "cli" || choice !== "file" || file !== t.file);
  const locked = busy || testing;

  /** The check a new token gets runs with `testing` set, so a second Test cannot start beside it; the dialog closes after. */
  const checked = async (lead: string) => {
    setTesting(true);
    await checkAndToast(a.name, lead);
    setTesting(false);
    if (!closed.current) onClose();
  };
  const save = async () => {
    setBusy(true);
    setError(null);
    const r = await request(path, jsonBody("PATCH", changes));
    setBusy(false);
    if (r.status !== 200) return setError(detailOf(r.body));
    onSaved();
    setAnswered(true);
    if (!tokenChanged) {
      close();
      return showToast(`Saved ${a.name}.`);
    }
    await checked(`Saved ${a.name}.`);
  };
  const replace = async () => {
    const value = paste;
    if (value === "" || locked) return;
    setPaste(""); // every answer, a refusal included, leaves the field empty
    setBusy(true);
    setError(null);
    const r = await putToken(a.name, value, pasteKind);
    setBusy(false);
    if (r.status !== 200) return setError(detailOf(r.body));
    onSaved();
    setAnswered(true);
    await checked(`Pasted a new token for ${a.name}.`);
  };
  const test = async () => {
    if (locked) return; // aria-disabled, not disabled: a disabled button that has focus sends it to <body>
    setTesting(true);
    setError(null);
    const r = await request<{ output: string }>(`${path}/test`, { method: "POST" });
    setTesting(false);
    if (r.status !== 200) return setError(detailOf(r.body));
    setOutput(r.body.output);
    onSaved();
  };

  return (
    <Dialog
      title={`Edit ${a.name}`}
      onClose={close}
      dirty={busy || (!answered && (changed || paste !== ""))}
      className="set-wk-dlg"
      footer={
        <div className="set-wk-foot">
          <div className="set-wk-foot-row">
            <Button variant="danger" className="set-wk-remove" disabled={locked || named.length > 0} aria-describedby="set-fg-remove" onClick={() => { close(); onRemove(a.name); }}>Remove account</Button>
            <Button className="set-fg-test" aria-disabled={locked || undefined} onClick={test}>{testing ? "Testing…" : "Test"}</Button>
            <Button onClick={close} disabled={busy}>Cancel</Button>
            <Button variant="primary" disabled={!changed || locked} onClick={save}>Save</Button>
          </div>
          <p id="set-fg-remove" className="set-hint">{named.length ? NAMED_BY(named) : a.default_for_host ? DEFAULT_REMOVABLE : ""}</p>
        </div>
      }
    >
      <div className="set-wk-form">
        {error && <p className="set-error" role="alert">{error}</p>}
        <dl className="set-fg-fixed">
          <dt>Name</dt><dd className="set-mono">{a.name}</dd>
          <dt>Forge</dt><dd>{a.forge}</dd>
          <dt>Web address</dt><dd className="set-mono">{a.web_url}</dd>
        </dl>
        <p className="set-hint">{FIXED_FIELDS}</p>
        <form className="set-wk-form" onSubmit={(e) => { e.preventDefault(); if (changed && !locked) void save(); }}>
          <div className="set-wk-field" ref={clientRef}>
            <span className="field-label">Client</span>
            <Segmented label="Client" disabled={command} value={client} onChange={setClient} options={[{ value: "cli", label: "cli" }, { value: "api", label: "api" }]} />
          </div>
          {client === "cli" && a.client === "api" && <p className="set-hint">{TO_CLI_DROPS}</p>}
          <Field label="API address"><input value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} /></Field>
          <div className="set-wk-switch">
            <Switch label="Default for its host" checked={isDefault} onChange={setDefault} />
            <span className="field-label">Default for its host</span>
          </div>
          {api && <Field label="CA file"><input value={caFile} onChange={(e) => setCaFile(e.target.value)} /></Field>}
          {api && command && <CommandSource />}
          {api && !command && (
            <>
              {/* No paste choice here: cli to api is a Save first, then Replace token (a PUT alone, nothing to roll back). */}
              <TokenSourceField allowPaste={false} choice={choice} onChoice={setChoice} env={env} onEnv={setEnv} file={file} onFile={setFile} kind={kind} onKind={setKind} paste="" onPaste={() => {}} />
              {leavesFile && <p className="set-hint">{SWITCH_DELETES_PASTED}</p>}
            </>
          )}
        </form>
        {/* A command account gets no Replace token: a PUT would erase its command (the server refuses it too, 403).
            Its own form, so Enter in the new token replaces it and never saves the other edits. */}
        {a.client === "api" && !command && (
          <form className="set-wk-token set-fg-replace" onSubmit={(e) => { e.preventDefault(); void replace(); }}>
            <h3 className="set-fg-sub">Replace token</h3>
            <PasteInput label="New token" value={paste} onChange={setPaste} />
            <KindSelect label="New token kind" kind={pasteKind} onKind={setPasteKind} />
            <div><Button type="submit" aria-disabled={locked || paste === "" || undefined}>Replace token</Button></div>
          </form>
        )}
        <div role="status" aria-label="Test status" className="set-fg-live">
          {testing && <p className="set-hint">Testing {a.name}…</p>}
          {output !== null && <pre ref={outRef} className="set-wk-cmd" aria-label="Test output">{output}</pre>}
        </div>
      </div>
    </Dialog>
  );
}
