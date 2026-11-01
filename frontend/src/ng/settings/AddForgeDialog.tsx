import { useState } from "react";
import type { ForgeAccountRow } from "../../types";
import { detailOf, jsonBody, request } from "../http";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field } from "../ui/Field";
import { Segmented } from "../ui/Segmented";
import { Switch } from "../ui/Switch";
import { showToast } from "../ui/Toast";
import { checkAndToast, type Kind, putToken, sourceBody, type SourceChoice, TokenSourceField } from "./forgeForm";
import { API_ADDRESS_HINT, CA_FILE_HINT, COMMAND_ADD_HINT, NAME_RULE, WEB_ADDRESS_HINT } from "./forgeWords";

/** Add an account. A pasted token is a POST and then a PUT; a refused PUT removes the account again. */
export function AddForgeDialog({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [name, setName] = useState("");
  const [forge, setForge] = useState<"github" | "gitlab">("github");
  const [webUrl, setWebUrl] = useState("");
  const [client, setClient] = useState<"cli" | "api">("cli");
  const [apiUrl, setApiUrl] = useState("");
  const [isDefault, setDefault] = useState(false);
  const [caFile, setCaFile] = useState("");
  const [choice, setChoice] = useState<SourceChoice>("git-credential");
  const [env, setEnv] = useState("");
  const [file, setFile] = useState("");
  const [kind, setKind] = useState<Kind>("unknown");
  const [paste, setPaste] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const api = client === "api";
  const valid = name.trim() !== "" && webUrl.trim() !== "" && (!api || choice !== "env" || env.trim() !== "") && (!api || choice !== "file" || file.trim() !== "") && (!api || choice !== "paste" || paste !== "");
  const save = async () => {
    const n = name.trim();
    const pasted = api && choice === "paste" ? paste : null;
    setPaste(""); // before anything is sent: no answer, a refusal included, leaves the token in the field
    setBusy(true);
    setError(null);
    const token = api ? sourceBody(choice, env, file, kind) : null;
    const r = await request<ForgeAccountRow>("/forges", jsonBody("POST", {
      name: n, forge, web_url: webUrl.trim(), client, default_for_host: isDefault,
      ...(apiUrl.trim() && { api_url: apiUrl.trim() }),
      ...(api && caFile.trim() && { ca_file: caFile.trim() }),
      ...(token && { token }),
    }));
    if (r.status !== 201) {
      setBusy(false);
      return setError(detailOf(r.body));
    }
    if (pasted !== null) {
      const p = await putToken(n, pasted, kind);
      if (p.status !== 200) {
        const d = await request(`/forges/${encodeURIComponent(n)}`, { method: "DELETE" });
        setBusy(false);
        // A repos.yaml entry can already name the new account (409): then it stays, and the list shows it as it is.
        setError(d.status === 204 ? detailOf(p.body) : `${detailOf(p.body)} The account ${n} could not be removed again: ${detailOf(d.body)}`);
        if (d.status !== 204) onAdded();
        return;
      }
    }
    onAdded();
    onClose();
    if (pasted !== null) await checkAndToast(n, `Added ${n}.`);
    else showToast(`Added ${n}.`);
  };

  return (
    <Dialog
      title="Add forge account"
      onClose={onClose}
      dirty={busy}
      className="set-wk-dlg"
      footer={<>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="primary" disabled={!valid || busy} onClick={save}>{busy ? "Adding…" : "Add account"}</Button>
      </>}
    >
      <form className="set-wk-form" onSubmit={(e) => { e.preventDefault(); if (valid && !busy) void save(); }}>
        {error && <p className="set-error" role="alert">{error}</p>}
        <Field label="Name" hint={NAME_RULE}><input data-autofocus value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <div className="set-wk-field">
          <span className="field-label">Forge</span>
          <Segmented label="Forge" value={forge} onChange={setForge} options={[{ value: "github", label: "github" }, { value: "gitlab", label: "gitlab" }]} />
        </div>
        <Field label="Web address" hint={WEB_ADDRESS_HINT}><input value={webUrl} onChange={(e) => setWebUrl(e.target.value)} /></Field>
        <div className="set-wk-field">
          <span className="field-label">Client</span>
          <Segmented label="Client" value={client} onChange={(c) => { setPaste(""); setClient(c); }} options={[{ value: "cli", label: "cli" }, { value: "api", label: "api" }]} />
        </div>
        <Field label="API address" hint={API_ADDRESS_HINT}><input value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} /></Field>
        <div className="set-wk-switch">
          <Switch label="Default for its host" checked={isDefault} onChange={setDefault} />
          <span className="field-label">Default for its host</span>
        </div>
        {api && (
          <>
            <Field label="CA file" hint={CA_FILE_HINT}><input value={caFile} onChange={(e) => setCaFile(e.target.value)} /></Field>
            <TokenSourceField allowPaste choice={choice} onChoice={setChoice} env={env} onEnv={setEnv} file={file} onFile={setFile} kind={kind} onKind={setKind} paste={paste} onPaste={setPaste} />
            <p className="set-hint">{COMMAND_ADD_HINT}</p>
          </>
        )}
      </form>
    </Dialog>
  );
}
