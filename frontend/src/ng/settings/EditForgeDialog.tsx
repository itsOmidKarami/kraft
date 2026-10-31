import { useState } from "react";
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
  const path = `/forges/${encodeURIComponent(a.name)}`;

  const api = client === "api";
  const tokenChanged = !command && api && (client !== a.client || choice !== first || env !== (t?.env ?? "") || file !== (t?.file ?? "") || kind !== (t?.kind ?? "unknown"));
  // A command account sends neither `token` nor `client`: see forgeForm's note, the UI is the only guard.
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

  const save = async () => {
    setBusy(true);
    setError(null);
    const r = await request(path, jsonBody("PATCH", changes));
    setBusy(false);
    if (r.status !== 200) return setError(detailOf(r.body));
    onSaved();
    onClose();
    if (tokenChanged) await checkAndToast(a.name, `Saved ${a.name}.`);
    else showToast(`Saved ${a.name}.`);
  };
  const replace = async () => {
    const value = paste;
    setPaste(""); // every answer, a refusal included, leaves the field empty
    setBusy(true);
    setError(null);
    const r = await putToken(a.name, value, pasteKind);
    setBusy(false);
    if (r.status !== 200) return setError(detailOf(r.body));
    onSaved();
    onClose();
    await checkAndToast(a.name, `Pasted a new token for ${a.name}.`);
  };
  const test = async () => {
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
      onClose={onClose}
      dirty={locked || changed || paste !== ""}
      className="set-wk-dlg"
      footer={
        <div className="set-wk-foot">
          <div className="set-wk-foot-row">
            <Button variant="danger" className="set-wk-remove" disabled={locked || named.length > 0} aria-describedby="set-fg-remove" onClick={() => { onClose(); onRemove(a.name); }}>Remove account</Button>
            <Button onClick={test} disabled={locked}>{testing ? "Testing…" : "Test"}</Button>
            <Button onClick={onClose} disabled={locked}>Cancel</Button>
            <Button variant="primary" disabled={!changed || locked} onClick={save}>Save</Button>
          </div>
          <p id="set-fg-remove" className="set-hint">{named.length ? NAMED_BY(named) : a.default_for_host ? DEFAULT_REMOVABLE : ""}</p>
        </div>
      }
    >
      <form className="set-wk-form" onSubmit={(e) => { e.preventDefault(); if (changed && !locked) void save(); }}>
        {error && <p className="set-error" role="alert">{error}</p>}
        <Field label="Name"><input readOnly value={a.name} /></Field>
        <div className="set-wk-pair">
          <Field label="Forge"><input readOnly value={a.forge} /></Field>
          <Field label="Web address"><input readOnly value={a.web_url} /></Field>
        </div>
        <p className="set-hint">{FIXED_FIELDS}</p>
        <div className="set-wk-field">
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
        {api && command && <CommandSource name={a.name} command={t?.command ?? ""} />}
        {api && !command && (
          <>
            {/* No paste choice here: cli to api is a Save first, then Replace token (a PUT alone, nothing to roll back). */}
            <TokenSourceField allowPaste={false} choice={choice} onChoice={setChoice} env={env} onEnv={setEnv} file={file} onFile={setFile} kind={kind} onKind={setKind} paste="" onPaste={() => {}} />
            {leavesFile && <p className="set-hint">{SWITCH_DELETES_PASTED}</p>}
          </>
        )}
        {/* A command account gets no Replace token, and a PUT would erase its command: the UI is the only guard. */}
        {a.client === "api" && !command && (
          <div className="set-wk-token set-fg-replace">
            <PasteInput label="New token" value={paste} onChange={setPaste} />
            <KindSelect label="New token kind" kind={pasteKind} onKind={setPasteKind} />
            <div><Button disabled={locked || paste === ""} onClick={replace}>Replace token</Button></div>
          </div>
        )}
        {output !== null && <pre className="set-wk-cmd" aria-label="Test output">{output}</pre>}
      </form>
    </Dialog>
  );
}
